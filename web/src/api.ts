export type Workspace = { id: string; name: string; role: 'owner' | 'member' }
export type Source = { name: string; relative_path: string }
export type KnowledgeBase = { id: string; name: string }
export type Document = { id: string; filename: string; chunks: number }
export type Workflow = { id: string; name: string; version: number }
export type Run = { id: string; kind: string; status: string; created_at: string; answer?: unknown; workflow_id?: string | null }
export type RunEvent = { id: string; step_id: string; kind: string; created_at: string; payload: Record<string, unknown> }
export type SearchHit = { chunk_id: string; filename: string; ordinal: number; text: string; score: number }
export type ConversationTurn = { id: string; question: string; answer: string | null; run_id: string; status: string; created_at: string }
export type Conversation = { id: string; workspace_id: string; owner_id: string; source_name: string; knowledge_base_id: string | null; title: string; created_at: string; updated_at: string; turns?: ConversationTurn[] }
export type AgentStreamEvent = { type: string; run_id?: string; conversation_id?: string; text?: string; answer?: string; tool?: string; tool_count?: number; reason?: string; message?: string }
export type PlatformResource = { id: string; name: string; kind: string; version: number; payload: Record<string, unknown>; enabled: boolean; updated_at: string }
export type PlatformFile = { id: string; filename: string; content_type: string; size: number; sha256: string; created_at: string }

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = sessionStorage.getItem('dap_token')
  const headers: Record<string, string> = { ...(options.headers as Record<string, string> | undefined) }
  if (token) headers.Authorization = `Bearer ${token}`
  if (options.body && typeof options.body === 'string') headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch(`/v1${path}`, { ...options, headers })
  } catch {
    throw new Error('连接不到后端。请先启动本地接口服务。')
  }
  const result = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = typeof result.detail === 'string' ? result.detail : `请求失败（${response.status}）`
    if (response.status === 401) throw new Error('登录已失效，请重新登录。')
    if (response.status === 503) throw new Error('模型密钥尚未配置。请在后端终端设置所需环境变量。')
    throw new Error(detail)
  }
  return result as T
}

export function json(body: unknown): RequestInit {
  return { method: 'POST', body: JSON.stringify(body) }
}

export async function apiStream(path: string, body: unknown, onEvent: (event: AgentStreamEvent) => void): Promise<void> {
  const token = sessionStorage.getItem('dap_token')
  let response: Response
  try {
    response = await fetch(`/v1${path}`, {
      method: 'POST', headers: {
        'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}),
      }, body: JSON.stringify(body),
    })
  } catch {
    throw new Error('连接不到后端。请先启动本地接口服务。')
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    if (response.status === 401) throw new Error('登录已失效，请重新登录。')
    if (response.status === 503) throw new Error('模型密钥或流式调用尚未配置。请检查后端环境变量。')
    throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`)
  }
  if (!response.body) throw new Error('服务器未返回实时响应。')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false
  function parseBlock(block: string) {
    const lines = block.split('\n')
    const eventName = lines.find((line) => line.startsWith('event: '))?.slice(7)
    const data = lines.filter((line) => line.startsWith('data: ')).map((line) => line.slice(6)).join('\n')
    if (!eventName || !data) return
    let event: AgentStreamEvent
    try { event = JSON.parse(data) as AgentStreamEvent } catch { throw new Error('服务器返回了无法解析的实时事件。') }
    if (event.type !== eventName) throw new Error('服务器实时事件类型不一致。')
    onEvent(event)
    if (event.type === 'completed' || event.type === 'failed') terminal = true
    if (event.type === 'error') throw new Error(event.message || '分析中断，请查看运行轨迹。')
  }
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n')
      let end: number
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        parseBlock(buffer.slice(0, end))
        buffer = buffer.slice(end + 2)
      }
    }
    buffer += decoder.decode()
    if (buffer.trim()) parseBlock(buffer.trim())
    if (!terminal) throw new Error('实时连接提前结束，本次回答未完成。')
  } catch (error) {
    await reader.cancel().catch(() => undefined)
    throw error
  } finally {
    reader.releaseLock()
  }
}
