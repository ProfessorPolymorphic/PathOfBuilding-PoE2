import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, ApiError, type ToolCall } from '../api'
import { ChangeCard } from './ChangeCard'

export interface ChatEntry {
  role: 'user' | 'assistant' | 'error'
  text: string
  toolCalls?: ToolCall[]
  tokens?: number
}

const SUGGESTIONS = [
  'What are the biggest weaknesses of this build?',
  'Which notables within 5 points add the most DPS?',
  'Why is my lowest max hit so low?',
  'What config options are set, and are they realistic?',
]

// Tools that change the build; after these the stat panel should refresh.
const MUTATING = new Set(['set_config', 'set_main_skill'])

interface Props {
  buildId: string
  entries: ChatEntry[]
  setEntries: (update: (prev: ChatEntry[]) => ChatEntry[]) => void
  chatConfigured: boolean
  onBuildChanged: () => void
  onBuildLost: () => void
}

export function Chat({ buildId, entries, setEntries, chatConfigured, onBuildChanged, onBuildLost }: Props) {
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [progress, setProgress] = useState<Progress | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [entries, pending, progress?.calls.length])

  async function send(text: string) {
    const message = text.trim()
    if (!message || pending) return
    setDraft('')
    setEntries((prev) => [...prev, { role: 'user', text: message }])
    setPending(true)
    setProgress({ round: 0, calls: [], started: Date.now() })
    let finished = false
    try {
      await api.chatStream(buildId, message, (event) => {
        if (event.type === 'thinking') {
          setProgress((p) => p && { ...p, round: event.round })
        } else if (event.type === 'tool') {
          setProgress((p) => p && { ...p, calls: [...p.calls, event.call] })
          if (MUTATING.has(event.call.name) && event.call.ok) onBuildChanged()
        } else if (event.type === 'answer') {
          finished = true
          const { reply } = event
          setEntries((prev) => [...prev, {
            role: 'assistant', text: reply.answer, toolCalls: reply.tool_calls, tokens: reply.tokens,
          }])
        } else {
          finished = true
          setEntries((prev) => [...prev, { role: 'error', text: event.detail }])
        }
      })
      if (!finished) throw new Error('The connection closed before an answer arrived.')
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        onBuildLost()
        return
      }
      setEntries((prev) => [...prev, { role: 'error', text: err instanceof Error ? err.message : String(err) }])
    } finally {
      setPending(false)
      setProgress(null)
    }
  }

  async function reset() {
    await api.resetChat(buildId).catch(() => undefined)
    setEntries(() => [])
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send(draft)
    }
  }

  return (
    <section className="chat panel" aria-label="Build advisor chat">
      <header className="chat-head">
        <h2>Ask about your build</h2>
        {entries.length > 0 && (
          <button className="ghost small" onClick={reset} disabled={pending}>New conversation</button>
        )}
      </header>

      <div className="chat-log" aria-live="polite">
        {!chatConfigured && (
          <p className="error">Chat isn’t configured on the server. Set LLM_API_KEY and LLM_MODEL in app/.env.</p>
        )}
        {entries.length === 0 && chatConfigured && (
          <div className="suggestions">
            <p className="muted">Every number in an answer comes from the PoB engine. Try:</p>
            {SUGGESTIONS.map((s) => (
              <button key={s} className="suggestion" onClick={() => void send(s)}>{s}</button>
            ))}
          </div>
        )}
        {entries.map((e, i) => (
          <Entry key={i} entry={e} />
        ))}
        {pending && progress && <Working progress={progress} />}
        <div ref={endRef} />
      </div>

      <div className="composer">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={chatConfigured ? 'Ask a question… (Enter to send, Shift+Enter for a new line)' : 'Chat unavailable'}
          rows={2}
          disabled={!chatConfigured}
          aria-label="Message"
        />
        <button onClick={() => void send(draft)} disabled={pending || !draft.trim() || !chatConfigured}>Send</button>
      </div>
    </section>
  )
}

interface Progress {
  round: number
  calls: ToolCall[]
  started: number
}

// Live view while the agent works: elapsed time, model round, and engine calls so far.
function Working({ progress }: { progress: Progress }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const seconds = Math.max(0, Math.round((now - progress.started) / 1000))
  const step = progress.round <= 1 && progress.calls.length === 0
    ? 'Reading your question'
    : progress.calls.length > 0 && progress.round > 1
      ? 'Working through the engine results'
      : 'Thinking'
  return (
    <div className="msg assistant working" aria-live="polite">
      <div className="working-head">
        <span className="spinner" aria-hidden />
        <span>{step}…</span>
        <span className="muted">{seconds}s</span>
      </div>
      {progress.calls.length > 0 && (
        <ol className="working-calls">
          {progress.calls.map((c, i) => (
            <li key={i} className={c.ok ? '' : 'failed'}>
              <code>{c.name}</code>
              {Object.keys(c.args).length > 0 && <span className="tool-args">{formatArgs(c.args)}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function Entry({ entry }: { entry: ChatEntry }) {
  if (entry.role === 'user') return <div className="msg user">{entry.text}</div>
  if (entry.role === 'error') return <div className="msg error" role="alert">{entry.text}</div>
  const cards = (entry.toolCalls ?? []).filter((c) => c.ok && c.card)
  return (
    <div className="msg assistant">
      {entry.toolCalls && entry.toolCalls.length > 0 && <ToolTrace calls={entry.toolCalls} tokens={entry.tokens} />}
      <div className="markdown">
        <Markdown remarkPlugins={[remarkGfm]}>{entry.text}</Markdown>
      </div>
      {cards.length > 0 && (
        <div className="chat-cards">
          <h3>Tested in the engine</h3>
          {cards.map((c, i) => (
            <ChangeCard key={i} card={c.card!} title={c.card_title ?? c.name} subtitle="What-if, not applied"
              detail={c.card_detail} collapseUnchanged />
          ))}
        </div>
      )}
    </div>
  )
}

function ToolTrace({ calls, tokens }: { calls: ToolCall[]; tokens?: number }) {
  return (
    <details className="tool-trace">
      <summary>
        {calls.length} engine {calls.length === 1 ? 'call' : 'calls'}
        {tokens ? ` · ${tokens.toLocaleString()} tokens` : ''}
      </summary>
      <ol>
        {calls.map((c, i) => (
          <li key={i} className={c.ok ? '' : 'failed'}>
            <code>{c.name}</code>
            {Object.keys(c.args).length > 0 && <span className="tool-args">{formatArgs(c.args)}</span>}
            {!c.ok && <span className="tool-error"> — {c.summary}</span>}
          </li>
        ))}
      </ol>
    </details>
  )
}

function formatArgs(args: Record<string, unknown>): string {
  const s = Object.entries(args)
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(', ')
  return s.length > 140 ? `${s.slice(0, 137)}…` : s
}
