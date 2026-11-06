import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { api, apiStream, json, type Conversation, type ConversationTurn, type Document, type KnowledgeBase, type PlatformFile, type PlatformResource, type Run, type RunEvent, type SearchHit, type Source, type Workflow, type Workspace } from './api'

type Section = 'analysis' | 'conversations' | 'sources' | 'knowledge' | 'workflows' | 'platform' | 'runs'
type Notice = { text: string; tone: 'good' | 'bad' }

const sections: { id: Section; label: string; hint: string }[] = [
  { id: 'analysis', label: '分析工作台', hint: '提问与结论' },
  { id: 'conversations', label: '历史对话', hint: '续聊与回看' },
  { id: 'sources', label: '数据源', hint: '数据库结构' },
  { id: 'knowledge', label: '知识库', hint: '规则与文档' },
  { id: 'workflows', label: '工作流', hint: '步骤与审批' },
  { id: 'platform', label: '平台配置', hint: '模型与资源' },
  { id: 'runs', label: '运行记录', hint: '追踪与复盘' },
]

const exampleWorkflow = JSON.stringify({
  start: 'review',
  nodes: [
    { id: 'review', kind: 'approval', config: { output_key: 'approved' } },
    { id: 'accepted', kind: 'end', config: { result_key: 'approved' } },
    { id: 'rejected', kind: 'end', config: { result_key: 'approved' } },
  ],
  edges: [
    { source: 'review', target: 'accepted', label: 'true' },
    { source: 'review', target: 'rejected', label: 'false' },
  ],
}, null, 2)

function formatValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return '暂无结果'
  return JSON.stringify(value, null, 2)
}

function statusLabel(status: string): string {
  return ({ running: '运行中', waiting: '等待确认', succeeded: '已完成', failed: '失败' } as Record<string, string>)[status] || status
}

function traceValue(value: unknown): string {
  return value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function App() {
  const [token, setToken] = useState<string | null>(() => sessionStorage.getItem('dap_token'))
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [workspaceId, setWorkspaceId] = useState('')
  const [workspaceName, setWorkspaceName] = useState('')
  const [sources, setSources] = useState<Source[]>([])
  const [sourceName, setSourceName] = useState('')
  const [newSourceName, setNewSourceName] = useState('')
  const [sourcePath, setSourcePath] = useState('')
  const [schema, setSchema] = useState<{ table: string; columns: { name: string; type: string }[] }[]>([])
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [baseId, setBaseId] = useState('')
  const [analysisBaseId, setAnalysisBaseId] = useState('')
  const [baseName, setBaseName] = useState('')
  const [documents, setDocuments] = useState<Document[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [workflowName, setWorkflowName] = useState('')
  const [workflowSpec, setWorkflowSpec] = useState(exampleWorkflow)
  const [selectedWorkflow, setSelectedWorkflow] = useState('')
  const [runs, setRuns] = useState<Run[]>([])
  const [models, setModels] = useState<PlatformResource[]>([])
  const [platformFiles, setPlatformFiles] = useState<PlatformFile[]>([])
  const [resourceKind, setResourceKind] = useState('models')
  const [resourceName, setResourceName] = useState('')
  const [resourceConfig, setResourceConfig] = useState('{}')
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [question, setQuestion] = useState('')
  const [liveText, setLiveText] = useState('')
  const [livePhase, setLivePhase] = useState('')
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [selectedConversation, setSelectedConversation] = useState<Conversation | null>(null)
  const [conversationTurns, setConversationTurns] = useState<ConversationTurn[]>([])
  const [section, setSection] = useState<Section>('analysis')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)

  const workspace = useMemo(() => workspaces.find((item) => item.id === workspaceId), [workspaces, workspaceId])
  const owner = workspace?.role === 'owner'
  const basePath = workspaceId ? `/workspaces/${workspaceId}` : ''

  async function loadWorkspaces() {
    const result = await api<{ workspaces: Workspace[] }>('/workspaces')
    setWorkspaces(result.workspaces)
    setWorkspaceId((previous) => result.workspaces.some((item) => item.id === previous) ? previous : result.workspaces[0]?.id || '')
  }

  async function loadWorkspace(id: string) {
    const path = `/workspaces/${id}`
    const [sourceResult, baseResult, workflowResult, runResult, conversationResult, modelResult, fileResult] = await Promise.all([
      api<{ sources: Source[] }>(`${path}/sources`),
      api<{ knowledge_bases: KnowledgeBase[] }>(`${path}/knowledge-bases`),
      api<{ workflows: Workflow[] }>(`${path}/workflows`),
      api<{ runs: Run[] }>(`${path}/runs`),
      api<{ conversations: Conversation[] }>(`${path}/conversations`),
      api<{ models: PlatformResource[] }>(`${path}/models`),
      api<{ files: PlatformFile[] }>(`${path}/files`),
    ])
    setSources(sourceResult.sources)
    setSourceName((current) => sourceResult.sources.some((item) => item.name === current) ? current : sourceResult.sources[0]?.name || '')
    setBases(baseResult.knowledge_bases)
    setBaseId((current) => baseResult.knowledge_bases.some((item) => item.id === current) ? current : baseResult.knowledge_bases[0]?.id || '')
    setAnalysisBaseId((current) => baseResult.knowledge_bases.some((item) => item.id === current) ? current : '')
    setWorkflows(workflowResult.workflows)
    setSelectedWorkflow((current) => workflowResult.workflows.some((item) => item.id === current) ? current : workflowResult.workflows[0]?.id || '')
    setRuns(runResult.runs)
    setConversations(conversationResult.conversations)
    setModels(modelResult.models)
    setPlatformFiles(fileResult.files)
  }

  useEffect(() => {
    if (!token) return
    void loadWorkspaces().catch((error: Error) => setNotice({ text: error.message, tone: 'bad' }))
  }, [token])

  useEffect(() => {
    if (!token || !workspaceId) return
    setRun(null)
    setEvents([])
    setSelectedConversation(null)
    setConversationTurns([])
    void loadWorkspace(workspaceId).catch((error: Error) => setNotice({ text: error.message, tone: 'bad' }))
  }, [token, workspaceId])

  useEffect(() => {
    if (!token || !workspaceId || !baseId) {
      setDocuments([])
      return
    }
    void api<{ documents: Document[] }>(`${basePath}/knowledge-bases/${baseId}/documents`)
      .then((result) => setDocuments(result.documents))
      .catch((error: Error) => setNotice({ text: error.message, tone: 'bad' }))
  }, [token, workspaceId, baseId])

  async function action(work: () => Promise<void>, success?: string) {
    setBusy(true)
    setNotice(null)
    try {
      await work()
      if (success) setNotice({ text: success, tone: 'good' })
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : '操作失败，请稍后重试。', tone: 'bad' })
    } finally {
      setBusy(false)
    }
  }

  function login(event: FormEvent) {
    event.preventDefault()
    void action(async () => {
      const result = await api<{ access_token: string }>('/auth/login', json({ username, password }))
      sessionStorage.setItem('dap_token', result.access_token)
      setToken(result.access_token)
      setPassword('')
    })
  }

  function logout() {
    void api('/auth/logout', { method: 'POST' }).catch(() => undefined)
    sessionStorage.removeItem('dap_token')
    setToken(null)
    setWorkspaces([])
    setWorkspaceId('')
  }

  function createWorkspace(event: FormEvent) {
    event.preventDefault()
    void action(async () => {
      const result = await api<{ id: string }>('/workspaces', json({ name: workspaceName }))
      setWorkspaceName('')
      await loadWorkspaces()
      setWorkspaceId(result.id)
    }, '工作区已创建')
  }

  function registerSource(event: FormEvent) {
    event.preventDefault()
    void action(async () => {
      await api(`${basePath}/sources/${encodeURIComponent(newSourceName.trim())}`, json({ relative_path: sourcePath.trim() }))
      setNewSourceName('')
      setSourcePath('')
      await loadWorkspace(workspaceId)
    }, '数据源已登记')
  }

  function inspectSource(name: string) {
    setSourceName(name)
    void action(async () => {
      const result = await api<{ tables: typeof schema }>(`${basePath}/sources/${encodeURIComponent(name)}/schema`)
      setSchema(result.tables)
    })
  }

  async function showRun(runId: string) {
    const [detail, trace] = await Promise.all([
      api<Run>(`${basePath}/runs/${runId}`),
      api<{ events: RunEvent[] }>(`${basePath}/runs/${runId}/events`),
    ])
    setRun(detail)
    setEvents(trace.events)
    const latest = await api<{ runs: Run[] }>(`${basePath}/runs`)
    setRuns(latest.runs)
  }

  async function openConversation(id: string) {
    const detail = await api<Conversation>(`${basePath}/conversations/${id}`)
    setSelectedConversation(detail)
    setConversationTurns(detail.turns || [])
    setSourceName(detail.source_name)
    setAnalysisBaseId(detail.knowledge_base_id || '')
    setRun(null)
    setEvents([])
    setSection('analysis')
  }

  function newConversation() {
    setSelectedConversation(null)
    setConversationTurns([])
    setRun(null)
    setEvents([])
    setQuestion('')
    setSection('analysis')
  }

  function deleteConversation(id: string) {
    if (!confirm('删除这段对话？对应的运行审计记录仍会保留。')) return
    void action(async () => {
      await api(`${basePath}/conversations/${id}`, { method: 'DELETE' })
      if (selectedConversation?.id === id) {
        setSelectedConversation(null)
        setConversationTurns([])
        setRun(null)
        setEvents([])
      }
      const result = await api<{ conversations: Conversation[] }>(`${basePath}/conversations`)
      setConversations(result.conversations)
    }, '对话已删除，运行审计记录仍保留')
  }

  function ask(event: FormEvent) {
    event.preventDefault()
    if (!sourceName) {
      setNotice({ text: '请先登记并选择一个数据源。', tone: 'bad' })
      return
    }
    void action(async () => {
      let currentRunId = ''
      let currentConversationId = ''
      setLiveText('')
      setLivePhase('正在连接模型')
      try {
        await apiStream(`${basePath}/sources/${encodeURIComponent(sourceName)}/ask/stream`,
          { question, knowledge_base_id: analysisBaseId || null, conversation_id: selectedConversation?.id || null },
          (item) => {
            if (item.type === 'run_started') {
              currentRunId = item.run_id || ''
              currentConversationId = item.conversation_id || ''
              setLivePhase('正在分析问题')
            } else if (item.type === 'text_delta') {
              setLivePhase('正在生成回答')
              setLiveText((previous) => previous + (item.text || ''))
            } else if (item.type === 'model_finished' && (item.tool_count || 0) > 0) {
              setLiveText('')
              setLivePhase('正在查询数据或资料')
            } else if (item.type === 'tool_finished') {
              setLivePhase('查询完成，继续分析')
            } else if (item.type === 'failed') {
              throw new Error(item.reason === 'turn limit' ? '分析步数已达到上限，请缩小问题范围。' : '分析未完成，请查看运行轨迹。')
            }
          })
        setQuestion('')
      } finally {
        if (currentConversationId) {
          const detail = await api<Conversation>(`${basePath}/conversations/${currentConversationId}`).catch(() => null)
          if (detail) {
            setSelectedConversation(detail)
            setConversationTurns(detail.turns || [])
          }
          const listed = await api<{ conversations: Conversation[] }>(`${basePath}/conversations`).catch(() => null)
          if (listed) setConversations(listed.conversations)
        }
        if (currentRunId) await showRun(currentRunId).catch(() => undefined)
        setLiveText('')
        setLivePhase('')
      }
    }, '分析完成')
  }

  function createBase(event: FormEvent) {
    event.preventDefault()
    void action(async () => {
      const result = await api<{ id: string }>(`${basePath}/knowledge-bases`, json({ name: baseName }))
      setBaseName('')
      await loadWorkspace(workspaceId)
      setBaseId(result.id)
    }, '知识库已创建')
  }

  function uploadDocument(file: File | undefined) {
    if (!file || !baseId) return
    void action(async () => {
      if (!/\.(txt|md)$/i.test(file.name)) throw new Error('目前只支持 .txt 和 .md 文件。')
      const text = await file.text()
      await api(`${basePath}/knowledge-bases/${baseId}/documents`, json({ filename: file.name, text }))
      const result = await api<{ documents: Document[] }>(`${basePath}/knowledge-bases/${baseId}/documents`)
      setDocuments(result.documents)
    }, '文档已导入')
  }

  function deleteDocument(id: string) {
    if (!confirm('删除这份文档及其检索索引？')) return
    void action(async () => {
      await api(`${basePath}/knowledge-bases/${baseId}/documents/${id}`, { method: 'DELETE' })
      const result = await api<{ documents: Document[] }>(`${basePath}/knowledge-bases/${baseId}/documents`)
      setDocuments(result.documents)
    }, '文档已删除')
  }

  function search(event: FormEvent) {
    event.preventDefault()
    void action(async () => {
      const result = await api<{ hits: SearchHit[] }>(`${basePath}/knowledge-bases/${baseId}/search?q=${encodeURIComponent(searchQuery)}`)
      setHits(result.hits)
    })
  }

  function createWorkflow(event: FormEvent) {
    event.preventDefault()
    void action(async () => {
      let spec: unknown
      try { spec = JSON.parse(workflowSpec) } catch { throw new Error('工作流 JSON 格式不正确。') }
      const result = await api<{ id: string }>(`${basePath}/workflows`, json({ name: workflowName, spec }))
      setWorkflowName('')
      await loadWorkspace(workspaceId)
      setSelectedWorkflow(result.id)
    }, '工作流已保存')
  }

  function createPlatformResource(event: FormEvent) {
    event.preventDefault()
    void action(async () => {
      let config: unknown
      try { config = JSON.parse(resourceConfig) } catch { throw new Error('资源配置必须是合法 JSON。') }
      if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('资源配置必须是 JSON 对象。')
      await api(`${basePath}/${resourceKind}`, json({ name: resourceName, config }))
      setResourceName('')
      setResourceConfig('{}')
      await loadWorkspace(workspaceId)
    }, '平台资源已保存')
  }

  function uploadPlatformFile(file: File | undefined) {
    if (!file) return
    void action(async () => {
      if (file.size > 10_000_000) throw new Error('文件不能超过 10 MB。')
      const content = await file.text()
      await api(`${basePath}/files`, json({ filename: file.name, content, content_type: file.type || 'text/plain' }))
      await loadWorkspace(workspaceId)
    }, '文件已上传')
  }

  function runWorkflow() {
    if (!selectedWorkflow) return
    void action(async () => {
      const result = await api<{ run_id: string }>(`${basePath}/workflows/${selectedWorkflow}/runs`,
        json({ inputs: {}, source_name: sourceName || null, knowledge_base_id: analysisBaseId || null }))
      await showRun(result.run_id)
    }, '工作流已启动')
  }

  function approve(decision: boolean) {
    if (!run || !run.workflow_id) return
    void action(async () => {
      await api(`${basePath}/workflows/${run.workflow_id}/runs/${run.id}/resume`, json({ approval: decision }))
      await showRun(run.id)
    }, decision ? '已通过确认' : '已拒绝')
  }

  if (!token) return (
    <main className="login-shell">
      <section className="login-story">
        <div className="brand brand-light"><span className="brand-mark"><i /><i /><i /></span><span>澄析<span className="brand-en"> / CLARITY</span></span></div>
        <div className="story-copy"><span className="eyebrow">数据智能工作台</span><h1>让每个结论<br />都有出处。</h1><p>把数据库查询、业务资料和执行过程放在同一个工作台。问题从哪里来，答案依据什么，一目了然。</p></div>
        <div className="story-trace"><span>提问</span><b>→</b><span>检索与查询</span><b>→</b><span>证据</span><b>→</b><span>结论</span></div>
      </section>
      <section className="login-panel"><div className="login-card"><span className="eyebrow">欢迎回来</span><h2>登录工作区</h2><p>使用本地创建的账号继续分析。</p><form onSubmit={login} className="form-stack"><label>用户名<input required value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="请输入用户名" /></label><label>密码<input required type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="请输入密码" /></label><button className="button primary wide" disabled={busy}>进入工作台 <span>↗</span></button></form>{notice && <p className={`notice ${notice.tone}`} role="status">{notice.text}</p>}<div className="login-help">首次使用？先在 F 盘项目目录运行 <code>python -m data_platform.api bootstrap-user</code> 创建账号。</div></div></section>
    </main>
  )

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><i /><i /><i /></span><span>澄析<span className="brand-en"> / CLARITY</span></span></div>
        <div className="sidebar-caption">数据智能平台 · 本地开发版</div>
        <div className="nav-label">工作空间</div>
        <nav aria-label="主导航">{sections.map((item) => <button key={item.id} className={`nav-item ${section === item.id ? 'active' : ''}`} onClick={() => setSection(item.id)}><span className="nav-dot" /><span><strong>{item.label}</strong><small>{item.hint}</small></span></button>)}</nav>
        <div className="sidebar-bottom"><span className="server-indicator" />仅限本机访问<button onClick={logout} className="logout-button">退出登录</button></div>
      </aside>

      <div className="main-area">
        <header className="topbar"><div><span className="crumb">工作区</span><span className="crumb-divider">/</span><strong>{workspace?.name || '尚未选择'}</strong><span className="crumb-divider">/</span><span>{sections.find((item) => item.id === section)?.label}</span></div><select aria-label="切换工作区" value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>{workspaces.length === 0 && <option value="">选择工作区</option>}{workspaces.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></header>
        {notice && <div className={`notice banner ${notice.tone}`} role="status"><span>{notice.text}</span><button aria-label="关闭提示" onClick={() => setNotice(null)}>×</button></div>}

        {!workspace ? <main className="content"><div className="page-heading"><span className="eyebrow">开始使用</span><h1>先创建一个工作区</h1><p>数据源、知识库和运行记录都会归属于这个工作区。</p></div><form onSubmit={createWorkspace} className="card inline-form"><label>工作区名称<input required value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} placeholder="例如：经营分析" /></label><button className="button primary" disabled={busy}>创建工作区</button></form></main> : (
          <div className="workspace-layout"><main className="content">
            {section === 'analysis' && <>
              <div className="page-heading"><span className="eyebrow">分析工作台</span><h1>从问题走到证据</h1><p>选择数据源，提出业务问题。分析完成后，右侧会显示每一步的执行轨迹。</p></div>
              <div className="conversation-context"><span>{selectedConversation ? `继续对话 · ${selectedConversation.title}` : '新对话'}</span>{selectedConversation && <button className="text-button" onClick={newConversation}>开启新对话</button>}</div>
              {conversationTurns.length > 0 && <div className="turn-list">{conversationTurns.map((turn) => <article className="turn-card" key={turn.id}><div className="turn-question"><span>提问</span><p>{turn.question}</p></div><div className="turn-answer"><span>回答</span><p>{turn.status === 'failed' ? '这次分析失败了，可继续提问；失败内容不会进入后续上下文。' : turn.answer || '暂无回答'}</p></div>{turn.run_id && <button className="text-button" onClick={() => void action(() => showRun(turn.run_id))}>查看执行轨迹</button>}</article>)}</div>}
              {busy && livePhase && <section className="live-answer" aria-label="实时分析进度"><span className="live-indicator" /><strong role="status">{livePhase}</strong><p>{liveText || '等待下一步结果…'}</p></section>}
              <form className="question-card" onSubmit={ask}><div className="question-options"><label>数据源<select value={sourceName} disabled={!!selectedConversation} onChange={(event) => setSourceName(event.target.value)}><option value="">选择数据库</option>{sources.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label><label>参考知识库<select value={analysisBaseId} disabled={!!selectedConversation} onChange={(event) => setAnalysisBaseId(event.target.value)}><option value="">不使用知识库</option>{bases.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div><label className="question-label" htmlFor="question">{selectedConversation ? '继续提问' : '你想知道什么？'}</label><textarea id="question" required value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：客户一共有多少人？退款规则是什么？" rows={5} /><div className="question-footer"><span>回答会关联查询和资料来源</span><button className="button primary" disabled={busy || !sourceName}>{selectedConversation ? '继续分析' : '开始分析'} <span>↗</span></button></div></form>
              {!sources.length && <div className="empty-hint">还没有数据源。请先进入“数据源”，登记放在 F 盘项目数据目录中的 SQLite 文件。</div>}
              {run && !selectedConversation && <section className="result-card"><div className="section-kicker">本次结果 <span className={`status ${run.status}`}>{statusLabel(run.status)}</span></div><h2>{run.kind === 'workflow' ? '工作流结果' : '分析结论'}</h2><pre>{formatValue(run.answer)}</pre></section>}
            </>}

            {section === 'conversations' && <><div className="page-heading"><span className="eyebrow">历史对话</span><h1>沿着上次的问题继续</h1><p>打开旧对话可接着提问；数据源和知识库保持创建时的选择。删除对话不会删除独立保存的运行审计记录。</p></div><button className="button primary" onClick={newConversation}>开启新对话</button><div className="conversation-list">{conversations.map((item) => <article className="conversation-row" key={item.id}><button onClick={() => void action(() => openConversation(item.id))}><span className="tile-symbol">◌</span><span><strong>{item.title}</strong><small>{item.source_name} · {new Date(item.updated_at).toLocaleString('zh-CN')}</small></span><span className="tile-arrow">↗</span></button><button className="text-button danger" onClick={() => deleteConversation(item.id)}>删除</button></article>)}{!conversations.length && <div className="empty-hint">还没有对话。先到分析工作台提出第一个问题。</div>}</div></>}

            {section === 'sources' && <><div className="page-heading"><span className="eyebrow">数据源</span><h1>管理分析数据</h1><p>把 SQLite 文件放在项目的 <code>data/sources</code> 目录，再登记相对路径。查询始终以只读方式执行。</p></div>{owner && <form className="card form-grid" onSubmit={registerSource}><h2>登记数据库</h2><label>显示名称<input required value={newSourceName} onChange={(event) => setNewSourceName(event.target.value)} placeholder="例如：销售数据" /></label><label>文件相对路径<input required value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="例如：sales.db" /></label><button className="button primary" disabled={busy}>登记并检查</button></form>}<div className="section-heading"><h2>已登记的数据源</h2><span>{sources.length} 个</span></div><div className="tile-list">{sources.map((item) => <button className={`source-tile ${sourceName === item.name ? 'selected' : ''}`} key={item.name} onClick={() => inspectSource(item.name)}><span className="tile-symbol">▤</span><span><strong>{item.name}</strong><small>{item.relative_path}</small></span><span className="tile-arrow">↗</span></button>)}{!sources.length && <div className="empty-hint">暂无数据源。先登记一个 SQLite 文件，再查看表结构。</div>}</div>{schema.length > 0 && <section className="card schema-card"><div className="section-kicker">当前数据源结构</div><h2>{sourceName}</h2>{schema.map((table) => <div className="schema-table" key={table.table}><strong>{table.table}</strong><div>{table.columns.map((column) => <span key={column.name}>{column.name} <small>{column.type || '未指定类型'}</small></span>)}</div></div>)}</section>}</>}

            {section === 'knowledge' && <><div className="page-heading"><span className="eyebrow">知识库</span><h1>把规则变成可检索的依据</h1><p>导入文本后，可以检索相关片段并查看文件来源。当前支持 .txt 和 .md。</p></div>{owner && <form className="card inline-form" onSubmit={createBase}><label>新知识库名称<input required value={baseName} onChange={(event) => setBaseName(event.target.value)} placeholder="例如：退款政策" /></label><button className="button primary" disabled={busy}>创建知识库</button></form>}<div className="section-heading"><h2>文档与检索</h2><select value={baseId} onChange={(event) => { setBaseId(event.target.value); setHits([]) }}><option value="">选择知识库</option>{bases.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>{baseId ? <><div className="card knowledge-actions">{owner && <label className="button secondary upload-button">导入文本文件<input type="file" accept=".txt,.md,text/plain,text/markdown" onChange={(event) => uploadDocument(event.target.files?.[0])} /></label>}<span>{documents.length} 份文档</span></div><div className="document-list">{documents.map((item) => <div key={item.id} className="document-row"><span className="tile-symbol">▤</span><span><strong>{item.filename}</strong><small>{item.chunks} 个文本块</small></span>{owner && <button className="text-button danger" onClick={() => deleteDocument(item.id)}>删除</button>}</div>)}</div><form className="card search-form" onSubmit={search}><label>检索资料<input required value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="例如：退款期限" /></label><button className="button primary" disabled={busy}>检索</button></form>{hits.map((hit) => <article className="hit-card" key={hit.chunk_id}><div className="hit-source">{hit.filename} · 第 {hit.ordinal + 1} 段</div><p>{hit.text}</p></article>)}</> : <div className="empty-hint">先创建或选择一个知识库，再导入文档。</div>}</>}

            {section === 'workflows' && <><div className="page-heading"><span className="eyebrow">工作流</span><h1>让分析过程可控</h1><p>按节点组织任务，遇到需要确认的步骤会暂停，确认后从保存的位置继续。</p></div><div className="section-heading"><h2>已保存的流程</h2><span>{workflows.length} 个</span></div><div className="tile-list">{workflows.map((item) => <button className={`source-tile ${selectedWorkflow === item.id ? 'selected' : ''}`} key={item.id} onClick={() => setSelectedWorkflow(item.id)}><span className="tile-symbol">◇</span><span><strong>{item.name}</strong><small>版本 {item.version}</small></span><span className="tile-arrow">↗</span></button>)}</div>{selectedWorkflow && <div className="workflow-actions"><button className="button primary" disabled={busy} onClick={runWorkflow}>运行所选工作流</button>{run?.status === 'waiting' && run.kind === 'workflow' && owner && <><button className="button secondary" disabled={busy} onClick={() => approve(true)}>通过确认</button><button className="button subtle" disabled={busy} onClick={() => approve(false)}>拒绝</button></>}</div>}{owner && <form className="card workflow-editor" onSubmit={createWorkflow}><div className="section-kicker">新建流程</div><h2>定义节点和连接</h2><label>流程名称<input required value={workflowName} onChange={(event) => setWorkflowName(event.target.value)} placeholder="例如：报告审批" /></label><label>流程定义 JSON<textarea required className="code-input" rows={16} value={workflowSpec} onChange={(event) => setWorkflowSpec(event.target.value)} /></label><button className="button primary" disabled={busy}>保存流程</button></form>}</>}

            {section === 'platform' && <><div className="page-heading"><span className="eyebrow">平台配置</span><h1>模型、工具与运行资源</h1><p>这些配置按工作区保存，先完成注册，再由 Agent 或工作流显式使用。</p></div><form className="card form-grid" onSubmit={createPlatformResource}><h2>注册平台资源</h2><label>资源类型<select value={resourceKind} onChange={(event) => setResourceKind(event.target.value)}><option value="models">模型</option><option value="connectors">连接器</option><option value="agents">Agent</option><option value="skills">Skill</option><option value="applications">应用</option><option value="prompts">提示词</option><option value="plugins">插件 / MCP</option><option value="schedules">定时任务</option></select></label><label>名称<input required value={resourceName} onChange={(event) => setResourceName(event.target.value)} placeholder="例如：本地 Qwen 分析模型" /></label><label>配置 JSON<textarea className="code-input" rows={8} value={resourceConfig} onChange={(event) => setResourceConfig(event.target.value)} /></label><button className="button primary" disabled={busy || !owner}>保存资源</button></form><div className="section-heading"><h2>已注册模型</h2><span>{models.length} 个</span></div><div className="tile-list">{models.map((item) => <div className="source-tile" key={item.id}><span className="tile-symbol">◈</span><span><strong>{item.name}</strong><small>版本 {item.version} · {String(item.payload.provider || '未指定提供商')}</small></span></div>)}{!models.length && <div className="empty-hint">还没有模型配置。可以先注册本地 Ollama＋Qwen 模型。</div>}</div><div className="section-heading"><h2>附件</h2><span>{platformFiles.length} 个</span></div>{owner && <label className="button secondary upload-button">上传文件<input type="file" onChange={(event) => uploadPlatformFile(event.target.files?.[0])} /></label>}<div className="document-list">{platformFiles.map((file) => <div className="document-row" key={file.id}><span className="tile-symbol">▤</span><span><strong>{file.filename}</strong><small>{file.size} bytes · {file.sha256.slice(0, 12)}</small></span></div>)}</div></>}

            {section === 'runs' && <><div className="page-heading"><span className="eyebrow">运行记录</span><h1>每一步都能回看</h1><p>选一条任务，右侧查看工具调用、状态变化与异常；敏感参数不会出现在执行轨迹中。</p></div><div className="run-list">{runs.map((item) => <button key={item.id} className={`run-row ${run?.id === item.id ? 'selected' : ''}`} onClick={() => void action(() => showRun(item.id))}><span className="run-kind">{item.kind === 'agent' ? '分析' : '工作流'}</span><span className="run-id">{item.id.slice(0, 8)}</span><span>{new Date(item.created_at).toLocaleString('zh-CN')}</span><span className={`status ${item.status}`}>{statusLabel(item.status)}</span></button>)}{!runs.length && <div className="empty-hint">还没有运行记录。完成一次分析或工作流后会出现在这里。</div>}</div>{run && <section className="result-card"><div className="section-kicker">任务 {run.id.slice(0, 8)}</div><pre>{formatValue(run.answer)}</pre></section>}</>}
          </main><aside className="evidence-rail"><div className="rail-header"><span className="eyebrow">执行证据</span><h2>运行轨迹</h2><p>模型决策和工具执行会按发生顺序记录在这里。</p></div>{run ? <><div className="rail-run"><span className={`status ${run.status}`}>{statusLabel(run.status)}</span><strong>{run.id.slice(0, 8)}</strong><small>{run.kind === 'agent' ? '智能分析任务' : '工作流任务'}</small></div><ol className="trace-list">{events.map((event) => <li key={event.id}><span className="trace-node" /><div><strong>{event.kind === 'model-finished' ? '模型完成一轮决策' : event.kind === 'tool-finished' || event.kind === 'tool-attempt' ? '工具执行' : event.kind === 'status-changed' ? '状态变化' : event.kind === 'approval-requested' ? '等待人工确认' : event.kind === 'approval-decided' ? '人工确认完成' : event.kind === 'workflow-completed' ? '流程完成' : event.kind === 'node-completed' ? '节点完成' : event.kind}</strong><small>{event.step_id} · {new Date(event.created_at).toLocaleTimeString('zh-CN')}</small><code>{Object.entries(event.payload).map(([key, value]) => `${key}: ${traceValue(value)}`).join(' · ') || '已记录'}</code></div></li>)}</ol></> : <div className="rail-empty"><span className="rail-empty-icon">◎</span><strong>等待一次任务</strong><p>提出问题或运行工作流后，这里会显示可以复盘的步骤。</p></div>}</aside></div>
        )}
      </div>
    </div>
  )
}

export default App
