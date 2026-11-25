import { useEffect, useState } from 'react'
import { api, type ChatSummary, type ChatTurn, type SnapshotListing } from '../api'
import { formatStat } from '../format'
import { Entry, type ChatEntry } from './Chat'

// The stats shown for each snapshot: enough to recognise it, not a full sidebar.
const KEY_STATS: { stat: string; label: string }[] = [
  { stat: 'Life', label: 'Life' },
  { stat: 'EnergyShield', label: 'Energy shield' },
  { stat: 'TotalEHP', label: 'Effective HP' },
  { stat: 'CombinedDPS', label: 'DPS' },
]

interface Props {
  character: string
  openSnapshotId: string | null
  onOpen: (snapshotId: string) => Promise<void>
}

export function HistoryView({ character, openSnapshotId, onOpen }: Props) {
  const [snaps, setSnaps] = useState<SnapshotListing[] | null>(null)
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [opening, setOpening] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    Promise.all([api.snapshots(character), api.chats(character)])
      .then(([s, c]) => {
        if (!live) return
        setSnaps([...s].reverse())  // newest first
        setChats(c)
        setError(null)
      })
      .catch((err) => live && setError(err instanceof Error ? err.message : String(err)))
    return () => { live = false }
  }, [character, openSnapshotId])

  async function open(id: string) {
    setOpening(id)
    try {
      await onOpen(id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setOpening(null)
    }
  }

  if (error) return <p className="error panel" role="alert">{error}</p>
  if (!snaps) return <p className="muted panel">Loading {character}’s history…</p>

  const known = new Set(snaps.map((s) => s.id))
  const loose = chats.filter((c) => !c.snapshot_id || !known.has(c.snapshot_id))

  return (
    <section className="history panel" aria-label={`${character}’s build history`}>
      <header className="history-head">
        <div>
          <h2>{character}</h2>
          <p className="muted small-print">
            {snaps.length} saved {snaps.length === 1 ? 'build' : 'builds'}, one for each distinct code you pasted.
            Opening an older one gives you a working copy to look at and ask about; it doesn’t change the history.
          </p>
        </div>
        <a href={api.snapshotsDownloadUrl(character)} download={`${character}-history.json`}>Download the data</a>
      </header>

      <ol className="snapshot-list">
        {snaps.map((s) => {
          const isOpen = s.id === openSnapshotId
          const about = chats.filter((c) => c.snapshot_id === s.id)
          return (
            <li key={s.id} className={isOpen ? 'snapshot open' : 'snapshot'}>
              <div className="snapshot-head">
                <div className="snapshot-title">
                  <strong>Level {s.level ?? '?'} {s.ascendancy ?? s.class}</strong>
                  <span className="muted">{s.main_skill ?? 'no main skill'}</span>
                  <time className="muted" dateTime={s.taken_at}>{formatWhen(s.taken_at)}</time>
                </div>
                {isOpen
                  ? <span className="badge">Open now</span>
                  : (
                    <button className="ghost small" onClick={() => void open(s.id)} disabled={opening !== null}>
                      {opening === s.id ? 'Opening…' : 'Open'}
                    </button>
                  )}
              </div>
              <dl className="snapshot-stats">
                {KEY_STATS.filter((k) => s.stats[k.stat] !== undefined).map((k) => (
                  <div key={k.stat}>
                    <dt>{k.label}</dt>
                    <dd>{formatStat(k.stat, s.stats[k.stat])}</dd>
                  </div>
                ))}
              </dl>
              {about.length > 0 && <ChatList chats={about} />}
            </li>
          )
        })}
      </ol>

      {loose.length > 0 && (
        <div className="loose-chats">
          <h3>Other conversations</h3>
          <p className="muted small-print">About builds whose saved snapshot was deleted or never recorded.</p>
          <ChatList chats={loose} />
        </div>
      )}
    </section>
  )
}

function ChatList({ chats }: { chats: ChatSummary[] }) {
  return (
    <ul className="past-chats">
      {chats.map((c) => <PastChat key={c.id} chat={c} />)}
    </ul>
  )
}

// A past conversation, read-only. Its turns are fetched the first time it's expanded.
function PastChat({ chat }: { chat: ChatSummary }) {
  const [turns, setTurns] = useState<ChatTurn[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  function onToggle(e: React.SyntheticEvent<HTMLDetailsElement>) {
    if (!e.currentTarget.open || turns) return
    api.conversation(chat.id)
      .then((c) => setTurns(c.turns))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }

  return (
    <li>
      <details className="past-chat" onToggle={onToggle}>
        <summary>
          <span className="past-chat-title">{chat.title}</span>
          <span className="muted">
            {formatWhen(chat.started_at)} · {chat.turns} {chat.turns === 1 ? 'question' : 'questions'}
          </span>
        </summary>
        <div className="past-chat-log">
          {error && <p className="error" role="alert">{error}</p>}
          {!turns && !error && <p className="muted">Loading…</p>}
          {turns?.flatMap(toEntries).map((e, i) => <Entry key={i} entry={e} />)}
        </div>
      </details>
    </li>
  )
}

function toEntries(t: ChatTurn): ChatEntry[] {
  const reply: ChatEntry = t.error !== null
    ? { role: 'error', text: t.error }
    : { role: 'assistant', text: t.answer ?? '', toolCalls: t.tool_calls, tokens: t.tokens ?? undefined }
  return [{ role: 'user', text: t.question }, reply]
}

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}
