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

export const api = {
  health: () => request<Health>('GET', '/health'),
  loadBuild: (code: string) =>
    request<{ build_id: string; summary: BuildSummary }>('POST', '/builds', { code }),
  summary: (id: string) => request<BuildSummary>('GET', `/builds/${id}`),
  chat: (id: string, message: string) => request<ChatReply>('POST', `/builds/${id}/chat`, { message }),
  resetChat: (id: string) => request<void>('DELETE', `/builds/${id}/chat`),
}
