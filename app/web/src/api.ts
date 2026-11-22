// Typed client for the FastAPI engine API (app/api/main.py).

export interface StatRow {
  stat: string
  label: string
  value: number | string
  lowerIsBetter?: boolean
}

export interface Stats {
  player: StatRow[]
  minion?: StatRow[]
}

export interface Gem {
  name: string
  level: number
  quality: number
  enabled: boolean
  support: boolean
}

export interface SkillGroup {
  index: number
  label?: string
  slot?: string
  activeSkill?: string
  enabled: boolean
  isMain: boolean
  gems: Gem[]
}

export interface EquippedItem {
  slot: string
  name: string
  rarity: string
  base: string
}

export interface BuildSummary {
  name: string
  class: string
  ascendancy?: string
  level: number
  treeVersion: string
  mainSkill?: string
  passivePoints: number
  ascendancyPoints: number
  keystones: string[]
  notables: string[]
  items: EquippedItem[]
  skills: SkillGroup[]
  stats: Stats
}

export interface ToolCall {
  name: string
  args: Record<string, unknown>
  ok: boolean
  summary: string
}

export interface ChatReply {
  answer: string
  tool_calls: ToolCall[]
  tokens: number
  model: string
}

export interface Health {
  ok: boolean
  chat_configured: boolean
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Can’t reach the engine API. Is it running on port 8000?')
  }
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : res.statusText
    throw new ApiError(res.status, detail)
  }
  return data as T
}

export type ChatEvent =
  | { type: 'thinking'; round: number }
  | { type: 'tool'; call: ToolCall }
  | { type: 'answer'; reply: ChatReply }
  | { type: 'error'; detail: string }

// Streams /chat/stream (server-sent events over a POST), calling onEvent as each arrives.
async function chatStream(id: string, message: string, onEvent: (e: ChatEvent) => void): Promise<void> {
  let res: Response
  try {
    res = await fetch(`/builds/${id}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    })
  } catch {
    throw new ApiError(0, 'Can’t reach the engine API. Is it running on port 8000?')
  }
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}))
    throw new ApiError(res.status, typeof data.detail === 'string' ? data.detail : res.statusText)
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      let kind = 'message'
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) kind = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      const payload = data ? JSON.parse(data) : {}
      if (kind === 'thinking') onEvent({ type: 'thinking', round: payload.round })
      else if (kind === 'tool') onEvent({ type: 'tool', call: payload as ToolCall })
      else if (kind === 'answer') onEvent({ type: 'answer', reply: payload as ChatReply })
      else if (kind === 'error') onEvent({ type: 'error', detail: payload.detail ?? 'unknown error' })
    }
  }
}

export const api = {
  chatStream,
  health: () => request<Health>('GET', '/health'),
  loadBuild: (code: string) =>
    request<{ build_id: string; summary: BuildSummary }>('POST', '/builds', { code }),
  summary: (id: string) => request<BuildSummary>('GET', `/builds/${id}`),
  chat: (id: string, message: string) => request<ChatReply>('POST', `/builds/${id}/chat`, { message }),
  resetChat: (id: string) => request<void>('DELETE', `/builds/${id}/chat`),
}
