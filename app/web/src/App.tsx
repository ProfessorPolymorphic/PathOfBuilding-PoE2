import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type BuildSummary } from './api'
import { BuildDetails } from './components/BuildDetails'
import { BuildLoader } from './components/BuildLoader'
import { Chat, type ChatEntry } from './components/Chat'
import { StatPanel } from './components/StatPanel'

const BUILD_KEY = 'pob.buildId'
const chatKey = (id: string) => `pob.chat.${id}`

// localStorage can be unavailable (private mode, blocked storage); never let that break the app.
function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}
function save(key: string, value: unknown) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* ignore */
  }
}

export default function App() {
  const [buildId, setBuildId] = useState<string | null>(() => load<string | null>(BUILD_KEY, null))
  const [build, setBuild] = useState<BuildSummary | null>(null)
  const [entries, setEntriesState] = useState<ChatEntry[]>([])
  const [chatConfigured, setChatConfigured] = useState(true)
  const [showLoader, setShowLoader] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    api.health().then((h) => setChatConfigured(h.chat_configured)).catch(() => undefined)
  }, [])

  // Restore the last build (the server keeps it in memory until it restarts).
  useEffect(() => {
    if (!buildId || build) return
    api.summary(buildId)
      .then((s) => {
        setBuild(s)
        setEntriesState(load<ChatEntry[]>(chatKey(buildId), []))
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setNotice('The server no longer has your previous build. Paste its code again to continue.')
        } else {
          setNotice(err instanceof Error ? err.message : String(err))
        }
        save(BUILD_KEY, null)
        setBuildId(null)
      })
  }, [buildId, build])

  const setEntries = useCallback((update: (prev: ChatEntry[]) => ChatEntry[]) => {
    setEntriesState((prev) => {
      const next = update(prev)
      if (buildId) save(chatKey(buildId), next)
      return next
    })
  }, [buildId])

  async function loadBuild(code: string) {
    const res = await api.loadBuild(code)
    if (buildId) save(chatKey(buildId), null)
    save(BUILD_KEY, res.build_id)
    setBuildId(res.build_id)
    setBuild(res.summary)
    setEntriesState([])
    setShowLoader(false)
    setNotice(null)
  }

  // The server no longer has this build (it was deleted, or its saved copy was lost).
  const buildLost = useCallback(() => {
    if (buildId) save(chatKey(buildId), null)
    save(BUILD_KEY, null)
    setBuildId(null)
    setBuild(null)
    setEntriesState([])
    setNotice('The server no longer has that build loaded. Paste its code again to continue.')
  }, [buildId])

  const refreshBuild = useCallback(() => {
    if (!buildId) return
    api.summary(buildId).then(setBuild).catch((err) => {
      if (err instanceof ApiError && err.status === 404) buildLost()
    })
  }, [buildId, buildLost])

  const loading = buildId && !build

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>◈</span>
          <span>Build Advisor</span>
          <span className="muted">PoE2 · Path of Building engine</span>
        </div>
        {build && (
          <div className="build-id">
            <strong>Level {build.level} {build.ascendancy ?? build.class}</strong>
            <span className="muted">{build.mainSkill ? `${build.mainSkill} · ` : ''}{build.class}</span>
            <button className="ghost small" onClick={() => setShowLoader(true)}>Load another build</button>
          </div>
        )}
      </header>

      {notice && <p className="notice" role="status">{notice}</p>}

      {loading && <p className="muted center">Loading build…</p>}

      {!loading && (!build || showLoader) && (
        <main className="empty">
          <BuildLoader onLoad={loadBuild} onCancel={build ? () => setShowLoader(false) : undefined} />
        </main>
      )}

      {build && !showLoader && buildId && (
        <main className="workspace">
          <aside className="sidebar">
            <StatPanel stats={build.stats} />
            <BuildDetails build={build} />
          </aside>
          <Chat
            buildId={buildId}
            entries={entries}
            setEntries={setEntries}
            chatConfigured={chatConfigured}
            onBuildChanged={refreshBuild}
            onBuildLost={buildLost}
          />
        </main>
      )}
    </div>
  )
}
