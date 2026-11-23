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

// Change preview card (engine/worker.lua buildCard). Numbers are engine output, untouched.
export interface CardCell {
  key: string
  label: string
  tone?: 'fire' | 'cold' | 'lightning' | 'chaos' | 'physical'
  unit?: '%'
  before?: number | string
  after?: number | string
  delta?: number   // absent when unchanged
  pct?: number
  better?: boolean
}

export interface CardRow {
  label?: string   // set when several stats share the row (resistances, max hit)
  cells: CardCell[]
}

export interface CardGroup {
  group: 'Damage' | 'Pools' | 'Mitigation' | 'Survival'
  rows: CardRow[]
}

// A changed sidebar stat outside the core set.
export interface OtherChange {
  stat: string
  label: string
  before: number | string
  after: number | string
  delta: number | string
  pct?: number | string
  better: boolean
}

export interface Card {
  core: CardGroup[]
  other: OtherChange[]
}

export interface NodeCandidate {
  id: number
  name: string
  type: string
  cost: number
  path: string[]
  stats: string[]
  card: Card
}

export interface Dashboard {
  build: { name: string; class: string; ascendancy?: string; level: number; mainSkill?: string }
  generated_at: string
  baseline: Card
  nodes: { max_cost: number; types: string[]; evaluated: number; candidates: NodeCandidate[] }
}

// Hit distribution (api/distribution.py): sampled attempts of one skill per scenario.
export type Outcome = 'miss' | 'hit' | 'crit'

export interface RollRange { min: number; max: number; lucky: number; mitigation: number }

export interface HandModel {
  hitChance: number
  critChance: number
  critMultiplier?: number
  doubleDamageChance: number
  tripleDamageChance: number
  types: { type: string; hit?: RollRange; crit?: RollRange }[]
  pob: { averageHit: number; averageDamage: number }
}

export interface DistScenario {
  name: string
  config: Record<string, unknown>
  pob: { averageHit: number; averageDamage: number; totalDPS: number; speed: number }
  hands: { hand: string; model: HandModel }[]
  summary: {
    n: number
    mean_per_attempt: number
    mean_per_hit: number
    sample_mean: number
    quantiles: Record<string, number>
    min: number
    max: number
    share: Partial<Record<Outcome, number>>
  }
  samples: {
    damage: number[]
    outcome: Outcome[]
    multiplier: number[]
    hand: number[]
    by_type?: Record<string, number[]>
  }
}

export interface Distribution {
  skill: string
  group: number
  samples: number
  scenarios: DistScenario[]
}

export interface ScenarioRequest {
  name: string
  config?: Record<string, boolean | number | string | null>
}

export interface ConfigOption {
  var: string
  label: string
  type: string
  value?: unknown
}

export interface ToolCall {
  name: string
  args: Record<string, unknown>
  ok: boolean
  summary: string
  card?: Card
  card_title?: string
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
  dashboard: (id: string) => request<Dashboard>('GET', `/builds/${id}/dashboard`),
  dashboardDownloadUrl: (id: string) => `/builds/${id}/dashboard?download=true`,
  distribution: (id: string, body: { skill: string | number; scenarios: ScenarioRequest[]; samples?: number; by_type?: boolean }) =>
    request<Distribution>('POST', `/builds/${id}/distribution`, body),
  config: (id: string, query: string) =>
    request<{ options: ConfigOption[] }>('GET', `/builds/${id}/config?query=${encodeURIComponent(query)}`),
  chat: (id: string, message: string) => request<ChatReply>('POST', `/builds/${id}/chat`, { message }),
  resetChat: (id: string) => request<void>('DELETE', `/builds/${id}/chat`),
}
