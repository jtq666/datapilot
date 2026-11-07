import React, {useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import {Icon, Trace, Evidence, KnowledgePage, DataPage, Inbox, Answer, progressLabel, runErrors, statusLabel} from "./workbench.jsx";
import "./style.css";

const base = import.meta.env.VITE_API_URL || "http://127.0.0.1:18091";
const suggestions = [
  ["database", "查订单总量", "数据库里一共有多少笔订单？", "适合快速核对数据"],
  ["chart", "按渠道看订单", "按渠道统计订单数量和订单金额，用表格展示。", "按维度汇总业务数据"],
  ["book", "了解退款口径", "检索并解释退款率的业务定义，标注资料来源。", "从知识库查找规则"],
  ["file", "生成渠道退款报告", "按渠道计算退款率，列出订单数和退款订单数。", "选择指标规则并审批共享", "report"],
];
const nav = [["chat","message","智能问数"],["sources","database","数据源"],["knowledge","book","知识库"],["inbox","inbox","共享报告"]];

async function api(path, token, body, signal) {
  let response;
  try {
    response = await fetch(base + path, {
      signal, method: body === undefined ? "GET" : "POST",
      headers: {...(token ? {Authorization:"Bearer " + token} : {}), ...(body !== undefined ? {"Content-Type":"application/json"} : {})},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Error("无法连接工作台服务，请检查后端是否已启动。");
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const messages = {"Authentication required":"登录已过期，请重新登录。","Invalid credentials":"账号或密码有误。","Workspace membership required":"你没有访问这个工作区的权限。","Knowledge search unavailable":"检索服务暂时不可用。","Approval in progress":"审批正在处理，请稍后刷新。","Approval expired":"审批已过期。","Run is not awaiting approval":"这份报告当前无法审批。","Select a rule and an eligible recipient":"请选择指标规则和有效的接收人。","Approval recorded. Retry the same decision.":"决定已保存，请重试同一个审批操作。"};
    const error = new Error(messages[data.detail] || (typeof data.detail === "string" ? data.detail : "请求失败，请检查输入后重试。"));
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function Brand() {
  return <div className="brand"><span className="brandmark"><Icon name="brand" size={22}/></span><div>DataPilot<small>数据分析工作台</small></div></div>;
}

function Login({onLogin}) {
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const [localDemo,setLocalDemo] = useState(false);
  useEffect(()=>{api("/auth/options","").then(r=>setLocalDemo(r.local_demo)).catch(()=>{});},[]);
  async function enter(role) {
    setBusy(true);setError("");
    try { onLogin(await api("/auth/demo","",{role})); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  async function submit(e) {
    e.preventDefault(); setBusy(true); setError("");
    try { onLogin(await api("/auth/login","",Object.fromEntries(new FormData(e.currentTarget)))); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  return <div className="login-layout"><section className="login-intro"><Brand/><div className="login-copy"><span className="eyebrow">DATA WORKSPACE</span><h1>从一个问题开始，<br/>找到数据里的答案。</h1><p>查询订单与退款、检索指标规则，或将分析结果整理为可复核的报告。</p></div><span className="login-caption"><span className="status-dot"/> 数据分析工作区</span></section>
    <main className="login-main"><div className="login-form"><h2>{localDemo?"进入演示工作区":"登录工作区"}</h2>
      <p className="muted">{localDemo?"选择角色即可开始体验。":"使用你的 DataPilot 账号继续"}</p>
      {error && <div className="error-box" role="alert">{error}</div>}
      {localDemo && <div className="demo-actions">
        <button className="primary" disabled={busy} onClick={()=>enter("analyst")}><Icon name="chart" size={16}/>以分析员体验</button>
        <button disabled={busy} onClick={()=>enter("reviewer")}><Icon name="inbox" size={16}/>以报告接收人体验</button>
      </div>}
      {localDemo?<details className="password-entry"><summary>使用账号密码</summary><form onSubmit={submit}><LoginFields busy={busy}/></form></details>:
        <form onSubmit={submit}><LoginFields busy={busy}/></form>}
    </div></main>
  </div>;
}

function LoginFields({busy}) {
  return <><label>账号<input name="username" autoComplete="username" placeholder="输入账号" required/></label>
    <label>密码<input name="password" type="password" autoComplete="current-password" placeholder="输入密码" required/></label>
    <button className="primary" disabled={busy}>{busy ? "正在登录…" : "登录"}</button></>;
}

function ChatTurn({turn,activeTrace,onTrace,onApprove,onResume,onRetry,busy,progress}) {
  const evidence = turn.evidence || [];
  return <article className="turn">
    <div className="user-turn"><div>{turn.question}</div></div>
    <div className="assistant-turn"><span className="assistant-icon"><Icon name="brand" size={19}/></span><div className="assistant-body">
      <div className="assistant-head"><strong>DataPilot</strong><span className={"badge "+turn.status}>{statusLabel[turn.status] || "处理中"}</span></div>
      {turn.status==="running" && <div className="running-line"><span className="spinner"/>{progressLabel(progress)}</div>}
      {turn.error && <div className="error-box" role="status">{runErrors[turn.error] || "任务失败，查看执行步骤了解详情。"}</div>}
      {turn.answer && <Answer text={turn.answer}/>}
      {!!turn.rule?.length && <details className="rule-ref"><summary><Icon name="book" size={15}/> {turn.rule[0].title} <span>v{turn.rule[0].version}</span></summary>{turn.rule.map(c=><p key={c.chunk_id}>{c.text}</p>)}</details>}
      {evidence.length>0 && <Evidence evidence={evidence}/>}
      {turn.status==="waiting" && <div className="approval"><div><strong>确认后才会共享</strong><p>接收人 {turn.recipient} · 有效期至 {new Date(turn.deadline).toLocaleString("zh-CN")}</p></div>
        <div className="inline-actions"><button disabled={busy || turn.decision===true} onClick={()=>onApprove(turn.id,false)}>拒绝</button><button className="primary" disabled={busy || turn.decision===false} onClick={()=>onApprove(turn.id,true)}>{turn.decision===true?"继续共享":"确认共享"}</button></div>
      </div>}
      <div className="turn-actions">
        <button className={activeTrace?"selected":""} onClick={()=>onTrace(turn.id)}><Icon name="activity" size={15}/>执行步骤</button>
        {turn.status==="running" && <button onClick={()=>onResume(turn.id)} disabled={busy}><Icon name="refresh" size={15}/>尝试续接</button>}
        {turn.status==="failed" && <button onClick={()=>onRetry(turn.question)}><Icon name="refresh" size={15}/>重新编辑</button>}
      </div>
    </div></div>
  </article>;
}

function Workspace({session,onLogout}) {
  const [page,setPage] = useState("chat");
  const [sidebar,setSidebar] = useState(false);
  const [info,setInfo] = useState(null);
  const [conversations,setConversations] = useState([]);
  const [docs,setDocs] = useState([]);
  const [inbox,setInbox] = useState([]);
  const [conversation,setConversation] = useState(null);
  const [turns,setTurns] = useState([]);
  const [traceId,setTraceId] = useState(null);
  const [events,setEvents] = useState([]);
  const [latestEvent,setLatestEvent] = useState(null);
  const [question,setQuestion] = useState("");
  const [mode,setMode] = useState("ask");
  const [rule,setRule] = useState("");
  const [recipient,setRecipient] = useState("");
  const [busy,setBusy] = useState(false);
  const [loadingChat,setLoadingChat] = useState(false);
  const [error,setError] = useState("");
  const composer = useRef(null);
  const chatLog = useRef(null);
  const selection = useRef(0);
  const apiCall = (path,body,signal) => api(path,session.token,body,signal);
  const running = turns.find(t=>t.status==="running")?.id;
  const currentTitle = conversations.find(c=>c.id===conversation)?.title || turns[0]?.question || "智能问数";

  async function refresh() {
    const [workspace,history,knowledge,shares]=await Promise.all(["/workspace","/conversations","/knowledge","/shared"].map(p=>apiCall(p)));
    setInfo(workspace); setConversations(history); setDocs(knowledge); setInbox(shares);
  }
  function fail(err) {
    if (err.status===401) onLogout();
    else if (err.name!=="AbortError") setError(err.message);
  }
  async function action(fn) {
    setBusy(true); setError("");
    try { await fn(); } catch (err) { fail(err); } finally { setBusy(false); }
  }
  useEffect(()=>{
    refresh().catch(fail);
    const params=new URLSearchParams(location.search);
    const id=params.get("conversation"), view=params.get("view");
    if(id && /^[a-f0-9]{32}$/.test(id)) openConversation(id);
    else if(nav.some(n=>n[0]===view)) navigate(view);
  },[]);
  useEffect(()=>{
    if(loadingChat) return;
    const url=new URL(location.href);
    url.searchParams.delete("conversation");url.searchParams.delete("view");
    if(page==="chat" && conversation) url.searchParams.set("conversation",conversation);
    if(page!=="chat") url.searchParams.set("view",page);
    history.replaceState(null,"",url);
  },[page,conversation,loadingChat]);
  useEffect(()=>{
    if (!conversation || !running) return;
    const controller = new AbortController();
    let timer;
    async function poll() {
      try {
        const [data,steps] = await Promise.all([
          apiCall("/conversations/"+conversation,undefined,controller.signal),
          apiCall("/runs/"+running+"/events",undefined,controller.signal)
        ]);
        if (controller.signal.aborted) return;
        setTurns(data);
        setLatestEvent({run:running,event:steps.at(-1)});
        if (data.some(t=>t.status==="running")) timer=setTimeout(poll,1300);
        else refresh().catch(fail);
      } catch (err) {
        if (controller.signal.aborted) return;
        fail(err); timer=setTimeout(poll,3000);
      }
    }
    timer=setTimeout(poll,600);
    return ()=>{controller.abort();clearTimeout(timer);};
  },[conversation,running]);
  useEffect(()=>{
    if (!traceId) return;
    const controller = new AbortController();
    let timer;
    const poll = async () => {
      try {
        const data=await apiCall("/runs/"+traceId+"/events",undefined,controller.signal);
        if (controller.signal.aborted) return;
        setEvents(data);
      } catch (err) { if(!controller.signal.aborted) fail(err); }
      if (running===traceId && !controller.signal.aborted) timer=setTimeout(poll,1100);
    };
    poll();
    return ()=>{controller.abort();clearTimeout(timer);};
  },[traceId,running]);
  useEffect(()=>{
    if (chatLog.current) chatLog.current.scrollTop=chatLog.current.scrollHeight;
  },[conversation,turns.length,turns.at(-1)?.status,page]);

  async function openConversation(id) {
    const sequence=++selection.current;
    setPage("chat");setSidebar(false);setTraceId(null);setEvents([]);setError("");setLoadingChat(true);
    try {
      const data=await apiCall("/conversations/"+id);
      if(selection.current===sequence){setConversation(id);setTurns(data);setQuestion("");}
    } catch(err){if(selection.current===sequence)fail(err);}
    finally {if(selection.current===sequence)setLoadingChat(false);}
  }
  function newChat() {
    ++selection.current;
    setPage("chat");setSidebar(false);setConversation(null);setTurns([]);setTraceId(null);setEvents([]);
    setQuestion("");setMode("ask");setLoadingChat(false);setError("");
  }
  function openTrace(id) {
    setEvents([]);
    setTraceId(old=>old===id?null:id);
  }
  function navigate(next) {
    setPage(next);setSidebar(false);setError("");
    if(next==="inbox") apiCall("/shared").then(setInbox).catch(fail);
  }
  async function send() {
    if(!question.trim() || busy || running || loadingChat || !info) return;
    const sequence=selection.current;
    await action(async()=>{
      const payload={question:question.trim(),mode,source:info.source};
      if(conversation) payload.conversation=conversation;
      if(mode==="report") Object.assign(payload,{rule_key:rule,recipient});
      const next=await apiCall("/runs/start",payload);
      if(selection.current===sequence){
        setQuestion("");setConversation(next.conversation);setTurns(prev=>[...prev,next]);
        setEvents([]);setTraceId(null);
      }
      refresh().catch(fail);
    });
  }
  async function decide(id,approved) {
    const sequence=selection.current;
    await action(async()=>{
      const updated=await apiCall("/runs/"+id+"/approval",{approved});
      if(selection.current===sequence){
        setTurns(prev=>prev.map(item=>item.id===id?updated:item));
        if(traceId===id){const data=await apiCall("/runs/"+id+"/events");if(selection.current===sequence)setEvents(data);}
      }
      await refresh();
    });
  }
  const canSend=!!question.trim() && !running && !busy && !loadingChat && !!info && (mode==="ask" || !!rule && !!recipient);
  return <div className="shell">
    {sidebar && <button className="sidebar-overlay" aria-label="关闭导航" onClick={()=>setSidebar(false)}/>}
    <aside className={"sidebar "+(sidebar?"open":"")}>
      <Brand/><button className="new-chat" onClick={newChat}><Icon name="plus" size={18}/>新建对话</button>
      <nav aria-label="工作台">{nav.map(([id,icon,title])=><button className={page===id?"active":""} key={id} onClick={()=>navigate(id)}><Icon name={icon}/>{title}{id==="inbox" && inbox.length>0 && <span className="nav-count">{inbox.length}</span>}</button>)}</nav>
      <div className="history"><div className="history-label"><span>最近对话</span><Icon name="history" size={15}/></div>
        {conversations.length===0?<p className="history-empty">发出问题后，对话会保存在这里。</p>:conversations.map(c=><button className={page==="chat" && conversation===c.id?"active":""} key={c.id} title={c.title} onClick={()=>openConversation(c.id)}><Icon name="message" size={15}/><span>{c.title}</span></button>)}
      </div>
      <div className="profile"><span className="avatar">{session.username.slice(0,1).toUpperCase()}</span><div><strong>{session.username}</strong><small>演示工作区</small></div><button className="icon-button" title={session.demo?"切换角色":"退出登录"} aria-label={session.demo?"切换角色":"退出登录"} onClick={onLogout}><Icon name="logout"/></button></div>
    </aside>
    <main className="main">
      <header className="topbar"><div><button className="mobile-menu icon-button" aria-label="展开导航" onClick={()=>setSidebar(true)}><Icon name="menu"/></button><span className="breadcrumb">工作台 <span>/</span></span><strong>{page==="chat"?currentTitle:nav.find(n=>n[0]===page)[2]}</strong></div><span className="source-pill"><span className={"status-dot "+(info?"":"connecting")}/>{info?"订单与退款":"连接工作区"}</span></header>
      {error && <div className="banner error-box" role="alert">{error}<button className="icon-button" onClick={()=>setError("")} aria-label="关闭提示"><Icon name="close" size={16}/></button></div>}
      {page==="chat" ? <div className="chat-layout">
        <div className={"chat-column "+(!turns.length&&!loadingChat?"landing":"")}>
          <div className={"chat-log "+(!turns.length?"empty-chat":"")} ref={chatLog} aria-busy={loadingChat}>
            {loadingChat ? <p className="loading-text"><span className="spinner"/>读取会话中…</p> : turns.length===0 ? <section className="welcome"><div className="welcome-title"><span className="welcome-icon"><Icon name="brand" size={26}/></span><h1>DataPilot 数据分析助手</h1></div><p>从自然语言问题到可核对的数据结论</p></section> :
              <section className="thread" aria-label="对话记录">{turns.map(t=><ChatTurn key={t.id} turn={t} busy={busy} progress={latestEvent?.run===t.id?latestEvent.event:null} activeTrace={traceId===t.id} onTrace={openTrace} onApprove={decide}
                onResume={id=>action(()=>apiCall("/runs/"+id+"/resume",{}))}
                onRetry={text=>{setQuestion(text);composer.current?.focus();}}/>)}</section>}
          </div>
          <div className="composer-area"><form className="composer" onSubmit={e=>{e.preventDefault();send();}}>
            {mode==="report" && <div className="composer-config">
              <label>指标规则<select value={rule} onChange={e=>setRule(e.target.value)} required><option value="">选择规则</option>{docs.map(d=><option key={d.key} value={d.key}>{d.title} · v{d.version}</option>)}</select></label>
              <label>共享给<select value={recipient} onChange={e=>setRecipient(e.target.value)} required><option value="">选择接收人</option>{info?.recipients.map(n=><option key={n}>{n}</option>)}</select></label>
            </div>}
            {mode==="report" && docs.length===0 && <button type="button" className="text-button" onClick={()=>navigate("knowledge")}>先到知识库发布指标规则</button>}
            <textarea ref={composer} value={question} onChange={e=>setQuestion(e.target.value)} rows={2} maxLength={1500} aria-label="分析问题"
              placeholder="输入你的数据问题，Enter 发送，Shift + Enter 换行"
              onKeyDown={e=>{if(e.key==="Enter" && !e.shiftKey && !e.nativeEvent.isComposing){e.preventDefault();if(canSend)send();}}}/>
            <div className="composer-tools"><div><label className="mode-select"><Icon name={mode==="ask"?"message":"file"} size={16}/><select aria-label="任务模式" value={mode} onChange={e=>setMode(e.target.value)}><option value="ask">数据库问答</option><option value="report">生成共享报告</option></select></label><span className="composer-db"><Icon name="database" size={15}/>订单与退款</span></div>
              <button className="send" type="submit" aria-label="发送问题" title="发送问题" disabled={!canSend}><Icon name="send" size={20}/></button>
            </div>
          </form>
            {!turns.length&&!loadingChat ? <div className="examples"><div className="examples-title">推荐示例</div><div className="suggestions">{suggestions.map(([icon,title,q,desc,target])=><button type="button" key={title} onClick={()=>{setMode(target||"ask");setQuestion(q);composer.current?.focus();}}><span className="suggest-icon"><Icon name={icon}/></span><div><strong>{title}</strong><small>{desc}</small></div></button>)}</div></div> : <p className="composer-hint">AI 生成的分析请结合 SQL 证据核对。</p>}
          </div>
        </div>
        {traceId && <Trace events={events} runId={traceId} running={running===traceId} onClose={()=>openTrace(traceId)}/>}
      </div> : <div className="management">
        {page==="sources" && <DataPage apiCall={apiCall} onAsk={()=>{newChat();setQuestion(suggestions[0][2]);}}/>}
        {page==="knowledge" && <KnowledgePage apiCall={apiCall} docs={docs} canPublish={info?.can_publish} onPublished={()=>refresh()}/>}
        {page==="inbox" && <Inbox apiCall={apiCall} rows={inbox}/>}
      </div>}
    </main>
  </div>;
}

function App() {
  const [session,setSession]=useState(()=>({token:sessionStorage.getItem("dp-token")||"",username:sessionStorage.getItem("dp-name")||"",demo:sessionStorage.getItem("dp-demo")==="1"}));
  function login(s){sessionStorage.setItem("dp-token",s.token);sessionStorage.setItem("dp-name",s.username);sessionStorage.setItem("dp-demo",s.demo?"1":"0");setSession(s);}
  function logout(){sessionStorage.removeItem("dp-token");sessionStorage.removeItem("dp-name");sessionStorage.removeItem("dp-demo");history.replaceState(null,"",location.pathname);setSession({token:"",username:""});}
  return session.token?<Workspace session={session} onLogout={logout}/>:<Login onLogin={login}/>;
}
createRoot(document.getElementById("root")).render(<App/>);
