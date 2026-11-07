import React, {useEffect,useState} from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

const paths={
  brand:<><path d="M4 18V7l8-4 8 4v11l-8 3-8-3Z"/><path d="m4 7 8 4 8-4m-8 4v10M8 9v5l4 2 4-2V9"/></>,
  plus:<path d="M12 5v14M5 12h14"/>,
  message:<path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l1.7-3.3A7.5 7.5 0 1 1 20 11.5Z"/>,
  database:<><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></>,
  book:<><path d="M12 6c-3-2-6-2-9-1v13c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Z"/><path d="M12 6v13"/></>,
  inbox:<path d="M4 4h16l2 9v7H2v-7l2-9Zm-2 9h6l2 3h4l2-3h6"/>,
  history:<><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></>,
  logout:<path d="M11 5H4v14h7m2-7h8m-3-3 3 3-3 3"/>,
  activity:<path d="M3 12h4l3-7 4 14 3-7h4"/>,
  refresh:<path d="M19 8a8 8 0 1 0 1 8M19 4v5h-5"/>,
  close:<path d="m6 6 12 12M18 6 6 18"/>,
  menu:<path d="M4 6h16M4 12h16M4 18h16"/>,
  file:<path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6m-6 4h6"/>,
  send:<path d="m12 19 0-14m-6 6 6-6 6 6"/>,
  chart:<path d="M4 4v16h16M8 16v-4m4 4V8m4 8v-6"/>,
  search:<><circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 5 5"/></>,
  copy:<><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M15 8V5H5v10h3"/></>,
  download:<path d="M12 4v11m-4-4 4 4 4-4M5 17v3h14v-3"/>,
};
export function Icon({name,size=18}) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
export const statusLabel={clarifying:"待确认口径",waiting:"待共享",shared:"已共享",rejected:"已取消共享",expired:"已过期",running:"进行中",completed:"已完成",failed:"失败"};
export const runErrors={missing_rule:"没有找到这项指标的生效规则。请先到知识库发布规则。",missing_sql_evidence:"报告缺少查询结果。可尝试说明要查询的指标和时间范围。",model_unavailable:"模型服务暂时不可用，可以重新发起分析。",model_protocol_error:"模型返回异常，查看执行步骤可了解发生在哪一轮。",knowledge_unavailable:"知识检索暂时不可用。",tool_error_limit:"工具连续失败，任务已停止。可调整问题后重试。",iteration_limit:"分析轮次已达到上限。可以缩小问题范围后重试。",unauthorized_table:"查询涉及未经授权的表。请在数据源页面查看可用表。",permission_denied:"数据库拒绝了这次查询。",readonly_required:"工作台只支持只读查询。",unknown_tool:"模型请求的工具未注册。"};
const eventNames={knowledge_retrieved:"已检索业务规则",model_completed:"模型返回结果",tool_completed:"执行工具",report_ready:"分析结果已整理",approval_requested:"报告已生成，等待确认共享",approval_decided:"已收到共享决定",report_shared:"报告已共享",metric_clarification_requested:"等待用户确认指标口径",metric_clarification_answered:"已确认指标口径",model_failed:"模型调用失败",run_failed:"任务已停止"};
const tools={query_database:"查询数据库",database_schema:"查看表结构",search_knowledge:"检索知识库",clarify_metric:"确认指标口径"};

export function progressLabel(event) {
  if(!event) return "正在分析你的问题";
  if(event.kind==="model_completed" && event.detail.tools?.length)
    return "准备"+event.detail.tools.map(name=>tools[name]||"调用工具").join("、");
  if(event.kind==="tool_completed") {
    if(!event.detail.ok) return "正在处理工具错误";
    return "已"+(tools[event.detail.tool]||"完成工具调用")+"，正在整理下一步";
  }
  return eventNames[event.kind] || "正在分析你的问题";
}

export function Answer({text}) {
  return <div className="answer"><Markdown remarkPlugins={[remarkGfm]} skipHtml
    disallowedElements={["img","iframe"]} components={{table:({children})=><div className="table-wrap"><table>{children}</table></div>}}>{text}</Markdown></div>;
}

export function Trace({events,runId,running,onClose}) {
  return <aside className="trace-panel" aria-label="执行步骤"><div className="trace-heading"><div><h2>执行步骤</h2><small title={runId}>任务 {runId.slice(0,8)}</small></div><button className="icon-button" onClick={onClose} aria-label="关闭执行步骤"><Icon name="close"/></button></div>
    <ol className="timeline">{events.map((e,i)=><li key={i}><div className="timeline-node"/><div><strong>{eventNames[e.kind] || e.kind}{e.detail.tool && " · "+(tools[e.detail.tool]||e.detail.tool)}</strong>
      <p>{e.detail.error || e.detail.code || (e.detail.evidence_id ? "证据 "+e.detail.evidence_id : "")}{Number.isFinite(e.detail.duration_ms) && " · "+e.detail.duration_ms+" ms"}</p>
      <details><summary>事件详情</summary><pre>{JSON.stringify(e.detail,null,2)}</pre></details></div></li>)}</ol>
    {running && <p className="timeline-wait"><span className="spinner"/>等待下一步</p>}
    {!running && !events.length && <p className="muted">暂无可用的执行记录。</p>}
  </aside>;
}

function csvValue(value) {
  const raw=String(value??"");
  const safe=typeof value==="string" && /^[\s]*[=+\-@]/.test(raw) && !/^-?\d+(\.\d+)?$/.test(raw)?"'"+raw:raw;
  return '"'+safe.replaceAll('"','""')+'"';
}
function downloadCsv(item) {
  const content=[item.columns,...item.rows].map(row=>row.map(csvValue).join(",")).join("\r\n");
  const url=URL.createObjectURL(new Blob(["\ufeff",content],{type:"text/csv;charset=utf-8"}));
  const link=document.createElement("a");link.href=url;link.download="datapilot-"+item.id+".csv";link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function canChart(item) {
  const numeric=i=>item.rows.every(row=>row[i]!==null && row[i]!=="" && /^-?\d+(\.\d+)?$/.test(String(row[i])));
  if(item.rows.length<2 || item.rows.length>16 || item.columns.length<2 || !item.rows.some(row=>typeof row[0]==="string" && !/^-?\d+(\.\d+)?$/.test(row[0]))) return [];
  return item.columns.map((_,i)=>i).filter(i=>i>0 && numeric(i));
}
function Chart({item,indices}) {
  const [column,setColumn]=useState(indices[0]);
  const values=item.rows.map(row=>Number(row[column]));
  const min=Math.min(0,...values),max=Math.max(0,...values),range=max-min||1,zero=(-min/range)*100;
  return <div className="chart"><label>展示指标<select value={column} onChange={e=>setColumn(Number(e.target.value))}>{indices.map(i=><option key={i} value={i}>{item.columns[i]}</option>)}</select></label>
    {item.rows.map((row,i)=><div className="bar-row" key={i}><span title={String(row[0])}>{String(row[0])}</span><div className="bar-track"><div style={{left:zero+"%"}} className="bar-zero"/><div className="bar" style={{left:Math.min(zero,(values[i]-min)/range*100)+"%",width:Math.abs(values[i])/range*100+"%"}}/></div><code>{row[column]}</code></div>)}</div>;
}
function QueryEvidence({item}) {
  const [tab,setTab]=useState("table");
  const [copy,setCopy]=useState("");
  const indices=canChart(item);
  return <div className="query-evidence"><div className="evidence-bar"><div className="tabs" aria-label="查询结果视图">
    {[["table","结果表"],...(indices.length?[["chart","图表"]]:[]),["sql","SQL"]].map(([id,title])=><button key={id} className={tab===id?"active":""} aria-pressed={tab===id} onClick={()=>{setTab(id);setCopy("");}}>{title}</button>)}
    </div><button title="导出 CSV" className="icon-button" aria-label="导出 CSV" onClick={()=>downloadCsv(item)}><Icon name="download" size={16}/></button></div>
    {item.truncated && <p className="muted inset">结果已达到显示上限，导出包含当前返回的行。</p>}
    {tab==="table" && (item.rows.length ? <div className="table-wrap"><table><thead><tr>{item.columns.map((col,i)=><th key={i}>{col}</th>)}</tr></thead><tbody>{item.rows.map((row,i)=><tr key={i}>{row.map((value,j)=><td key={j}>{value===null?"NULL":String(value)}</td>)}</tr>)}</tbody></table></div>:<p className="inset muted">这次查询没有返回数据行。</p>)}
    {tab==="chart" && <Chart item={item} indices={indices}/>}
    {tab==="sql" && <div className="sql-view"><button onClick={async()=>{try{await navigator.clipboard.writeText(item.sql);setCopy("已复制");}catch{setCopy("无法复制，请手动选择 SQL");}}}><Icon name="copy" size={15}/>{copy||"复制 SQL"}</button><pre>{item.sql}</pre></div>}
  </div>;
}
export function Evidence({evidence}) {
  return <div className="evidence">{evidence.map((item,i)=><details key={item.id} open={i===evidence.length-1}><summary><Icon name="chart" size={16}/>{item.id} · 查询结果 <span>{item.rows.length} 行</span></summary><QueryEvidence item={item}/></details>)}</div>;
}
function PageTitle({title,desc,action}) {
  return <div className="page-title"><div><h1>{title}</h1><p>{desc}</p></div>{action}</div>;
}
function PageState({loading,error,onRetry}) {
  return error?<div className="error-box" role="alert">{error}{onRetry && <button onClick={onRetry}>重试</button>}</div>:loading?<p className="loading-text"><span className="spinner"/>正在读取…</p>:null;
}
export function DataPage({apiCall,onAsk}) {
  const [source,setSource]=useState(null),[schema,setSchema]=useState({}),[table,setTable]=useState(""),[error,setError]=useState(""),[loading,setLoading]=useState(true);
  function load() {
    setError("");setLoading(true);
    apiCall("/sources").then(async rows=>{
      const first=rows[0];setSource(first);
      if(first){const data=await apiCall("/sources/"+first.id+"/schema");setSchema(data);setTable(Object.keys(data)[0]||"");}
    }).catch(e=>setError(e.message)).finally(()=>setLoading(false));
  }
  useEffect(load,[]);
  return <section className="page"><PageTitle title="数据源" desc="查看当前工作区可查询的表与字段。" action={<button className="primary" onClick={onAsk}>用此数据源提问</button>}/>
    <PageState error={error} loading={loading} onRetry={load}/>
    {source && <div className="data-source"><span className="suggest-icon"><Icon name="database"/></span><div><strong>{source.label}</strong><p>{source.engine}</p></div><span className="badge completed">只读</span></div>}
    {!!Object.keys(schema).length && <div className="resource-layout"><div className="resource-list"><h2>数据表</h2>{Object.keys(schema).map(n=><button className={table===n?"active":""} key={n} onClick={()=>setTable(n)}><Icon name="database" size={15}/>{n}</button>)}</div>
      <div className="resource-detail"><h2>{table}</h2><div className="table-wrap"><table><thead><tr><th>字段</th><th>数据类型</th></tr></thead><tbody>{schema[table]?.map(c=><tr key={c.name}><td><code>{c.name}</code></td><td>{c.type}</td></tr>)}</tbody></table></div></div></div>}
  </section>;
}
export function KnowledgePage({apiCall,docs,canPublish,onPublished}) {
  const [panel,setPanel]=useState(""),[document,setDocument]=useState(null),[results,setResults]=useState(null),[search,setSearch]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
  async function run(fn){setBusy(true);setError("");setNotice("");try{await fn();}catch(e){setError(e.message);}finally{setBusy(false);}}
  const open=(key,version)=>run(async()=>{setDocument(await apiCall("/knowledge/"+encodeURIComponent(key)+(version?"?version="+version:"")));setPanel("detail");});
  return <section className="page"><PageTitle title="知识库" desc="检索业务口径，查看规则内容和历史版本。" action={canPublish && <button className="primary" onClick={()=>{setDocument(null);setPanel("publish");}}><Icon name="plus" size={16}/>发布规则</button>}/>
    <form className="search-box" onSubmit={e=>{e.preventDefault();if(search.trim())run(async()=>{setResults(await apiCall("/knowledge/search?q="+encodeURIComponent(search.trim())));setPanel("search");});}}>
      <Icon name="search"/><input aria-label="测试知识检索" placeholder="输入关键词或业务问题，测试知识检索" value={search} onChange={e=>setSearch(e.target.value)} maxLength={400} required/><button disabled={busy || !search.trim()}>检索</button>
    </form>
    <PageState error={error} loading={busy}/>{notice && <p className="notice" role="status">{notice}</p>}
    <div className="resource-layout"><div className="resource-list"><h2>生效文档</h2>{docs.length?docs.map(d=><button className={document?.key===d.key?"active":""} key={d.key} onClick={()=>open(d.key)} disabled={busy}><Icon name="file" size={15}/><span>{d.title}</span><small>v{d.version}</small></button>):<p className="muted">还没有生效的知识规则。</p>}</div>
      <div className="resource-detail">
        {panel==="publish"?<form key={document?.key || "new"} onSubmit={e=>{e.preventDefault();const payload=Object.fromEntries(new FormData(e.currentTarget));run(async()=>{const result=await apiCall("/knowledge",payload);await onPublished();setDocument(await apiCall("/knowledge/"+encodeURIComponent(result.key)));setPanel("detail");setNotice("已发布 v"+result.version);});}}>
          <h2>{document?"发布新版本":"发布规则"}</h2><label>规则标识<input name="key" required pattern="[a-z0-9_-]+" maxLength={80} defaultValue={document?.key} readOnly={!!document} placeholder="refund_rate"/></label>
          <label>标题<input name="title" required maxLength={120} defaultValue={document?.title} placeholder="退款率口径"/></label>
          <label>规则正文<textarea name="text" required maxLength={6500} rows={8} defaultValue={document?.text||""} placeholder="写明计算口径、数据来源与适用条件"/></label>
          <div className="inline-actions"><button type="button" onClick={()=>setPanel(document?"detail":"")}>取消</button><button className="primary" disabled={busy}>生成向量并发布</button></div>
        </form>:panel==="detail" && document?<><div className="resource-heading"><h2>{document.title}</h2>{canPublish && <button onClick={()=>setPanel("publish")}>发布新版本</button>}</div>
          <label>版本<select value={document.version} onChange={e=>open(document.key,Number(e.target.value))}>{document.versions.map(v=><option key={v.version} value={v.version}>v{v.version}{v.active?" · 当前生效":""}</option>)}</select></label>
          {document.chunks.map((c,i)=><article className="chunk" key={c.id}><span>切片 {i+1}</span><p>{c.text}</p></article>)}
        </>:panel==="search" && results?<><h2>检索结果</h2><p className="muted">关键词 {results.keyword_ids.length} 条 · 向量 {results.vector_ids.length} 条 · 融合 {results.matches.length} 条</p>
          {results.matches.map((c,i)=><article className="chunk" key={c.chunk_id}><button className="text-button" onClick={()=>open(c.key,c.version)}>{i+1}. {c.title} · v{c.version}</button><p>{c.text}</p></article>)}
          {!results.matches.length && <p className="muted">没有命中资料。可以先发布相关规则。</p>}</>:<div className="resource-empty"><Icon name="book" size={30}/><p>选择左侧文档查看内容，或用上方搜索框试查一条规则。</p></div>}
      </div>
    </div>
  </section>;
}
export function Inbox({apiCall,rows}) {
  const [item,setItem]=useState(null),[error,setError]=useState(""),[loading,setLoading]=useState(false);
  async function open(id){setError("");setLoading(true);try{setItem(await apiCall("/shared/"+id));}catch(e){setError(e.message);}finally{setLoading(false);}}
  return <section className="page"><PageTitle title="共享报告" desc="查看同事审批后共享给你的分析报告。"/>
    <PageState loading={loading} error={error}/>
    <div className="resource-layout"><div className="resource-list"><h2>收到的报告</h2>{rows.map(row=><button className={item?.id===row.id?"active":""} key={row.id} onClick={()=>open(row.id)} disabled={loading}><Icon name="file" size={15}/><span>{row.question}</span></button>)}
      {!rows.length && <p className="muted">当前没有收到报告。</p>}</div>
      <div className="resource-detail">{item?<><h2>{item.question}</h2><p className="muted">由 {item.owner} 共享</p><Answer text={item.answer}/>
        {item.rule?.length>0 && <details className="rule-ref"><summary>{item.rule[0].title} · v{item.rule[0].version}</summary>{item.rule.map(r=><p key={r.chunk_id}>{r.text}</p>)}</details>}
        {item.evidence?.length>0 && <Evidence evidence={item.evidence}/>}</>:<div className="resource-empty"><Icon name="inbox" size={30}/><p>打开一份报告查看分析和查询证据。</p></div>}</div>
    </div>
  </section>;
}
