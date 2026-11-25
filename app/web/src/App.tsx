import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type BuildSummary, type LoadedBuild } from './api'
import { BuildLoader } from './components/BuildLoader'
import { Chat, type ChatEntry } from './components/Chat'
import { Dashboard } from './components/Dashboard'
import { DefenceView } from './components/DefenceView'
import { DistributionView } from './components/DistributionView'
import { HistoryView } from './components/HistoryView'
import { ManaView } from './components/ManaView'

const BUILD_KEY = 'pob.buildId'
// Which character, and which of its saved snapshots, the open build came from.
const ORIGIN_KEY = 'pob.origin'
interface Origin { character: string | null; snapshotId: string | null }
const NO_ORIGIN: Origin = { character: null, snapshotId: null }
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

const TABS = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'distribution', label: 'Damage distribution' },
  { id: 'defences', label: 'Against enemy defences' },
  { id: 'mana', label: 'Mana' },
  { id: 'history', label: 'History' },
] as const
type Tab = (typeof TABS)[number]['id']

// The open tab lives in the URL hash, so reloading or bookmarking keeps it.
function tabFromHash(): Tab {
  const id = window.location.hash.slice(1)
  return TABS.some((t) => t.id === id) ? (id as Tab) : 'dashboard'
}

export default function App() {
  const [tab, setTabState] = useState<Tab>(tabFromHash)
  // Tabs stay mounted once opened, so switching back doesn't redo their work.
  const [opened, setOpened] = useState<Set<Tab>>(() => new Set([tabFromHash()]))
  const setTab = useCallback((t: Tab) => {
    setTabState(t)
    setOpened((prev) => (prev.has(t) ? prev : new Set(prev).add(t)))
    if (window.location.hash !== `#${t}`) history.replaceState(null, '', `#${t}`)
  }, [])
  useEffect(() => {
    const onHash = () => setTab(tabFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [setTab])
  const [buildId, setBuildId] = useState<string | null>(() => load<string | null>(BUILD_KEY, null))
  const [build, setBuild] = useState<BuildSummary | null>(null)
  const [entries, setEntriesState] = useState<ChatEntry[]>([])
  const [chatConfigured, setChatConfigured] = useState(true)
  const [showLoader, setShowLoader] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [origin, setOriginState] = useState<Origin>(() => load<Origin>(ORIGIN_KEY, NO_ORIGIN))
  const [characters, setCharacters] = useState<string[]>([])
  const [switching, setSwitching] = useState(false)

  const setOrigin = useCallback((o: Origin) => {
    setOriginState(o)
    save(ORIGIN_KEY, o.character ? o : null)
  }, [])

  const refreshCharacters = useCallback(() => {
    api.characters().then((cs) => setCharacters(cs.map((c) => c.character))).catch(() => undefined)
  }, [])

  useEffect(() => {
    api.health().then((h) => setChatConfigured(h.chat_configured)).catch(() => undefined)
    refreshCharacters()
  }, [refreshCharacters])

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

  // Make a freshly loaded build the open one. The previous working copy is dropped on the
  // server; its pasted snapshot and its chats stay in the history.
  function adopt(res: LoadedBuild) {
    if (buildId && buildId !== res.build_id) {
      save(chatKey(buildId), null)
      api.deleteBuild(buildId).catch(() => undefined)
    }
    save(BUILD_KEY, res.build_id)
    setBuildId(res.build_id)
    setBuild(res.summary)
    setOrigin({ character: res.character, snapshotId: res.snapshot?.id ?? null })
    setEntriesState([])
    setShowLoader(false)
    setNotice(null)
    refreshCharacters()
  }

  async function loadBuild(code: string, character: string) {
    adopt(await api.loadBuild(code, character))
  }

  async function openSnapshot(snapshotId: string) {
    adopt(await api.openSnapshot(snapshotId))
  }

  // Switching character opens its most recently pasted build.
  async function switchCharacter(name: string) {
    if (name === origin.character) return
    setSwitching(true)
    try {
      const snaps = await api.snapshots(name)
      const latest = snaps[snaps.length - 1]
      if (!latest) throw new Error(`${name} has no saved builds.`)
      await openSnapshot(latest.id)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err))
    } finally {
      setSwitching(false)
    }
  }

  // The server no longer has this build (it was deleted, or its saved copy was lost).
  const buildLost = useCallback(() => {
    if (buildId) save(chatKey(buildId), null)
    save(BUILD_KEY, null)
    setBuildId(null)
    setBuild(null)
    setEntriesState([])
    setNotice(origin.character
      ? `The server no longer has that build loaded. Open it again from ${origin.character}’s history, or paste its code.`
      : 'The server no longer has that build loaded. Paste its code again to continue.')
  }, [buildId, origin.character])

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
        {(build || characters.length > 0) && (
          <div className="build-id">
            {characters.length > 0 && (
              <label className="character-switch">
                <span>Character</span>
                <select
                  value={origin.character ?? ''}
                  onChange={(e) => void switchCharacter(e.target.value)}
                  disabled={switching}
                >
                  {!origin.character && <option value="" disabled>Choose…</option>}
                  {characters.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
            )}
            {build && (
              <>
                <strong>Level {build.level} {build.ascendancy ?? build.class}</strong>
                <span className="muted">{build.mainSkill ? `${build.mainSkill} · ` : ''}{build.class}</span>
              </>
            )}
            {switching && <span className="muted">Opening…</span>}
            {build && <button className="ghost small" onClick={() => setShowLoader(true)}>Paste a build</button>}
          </div>
        )}
      </header>

      {notice && <p className="notice" role="status">{notice}</p>}

      {loading && <p className="muted center">Loading build…</p>}

      {!loading && (!build || showLoader) && (
        <main className="empty">
          <BuildLoader
            onLoad={loadBuild}
            onCancel={build ? () => setShowLoader(false) : undefined}
            characters={characters}
            defaultCharacter={origin.character ?? undefined}
          />
        </main>
      )}

      {build && !showLoader && buildId && (
        <nav className="tabs" role="tablist" aria-label="Views">
          {TABS.filter((t) => t.id !== 'history' || origin.character).map((t) => (
            <button
              key={t.id}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              className={tab === t.id ? 'tab on' : 'tab'}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      )}

      {build && !showLoader && buildId && (
        <main className="workspace">
          <div className="tab-panels">
            {opened.has('dashboard') && (
              <div role="tabpanel" id="panel-dashboard" aria-labelledby="tab-dashboard" hidden={tab !== 'dashboard'}>
                <Dashboard buildId={buildId} build={build} onBuildLost={buildLost} />
              </div>
            )}
            {opened.has('distribution') && (
              <div role="tabpanel" id="panel-distribution" aria-labelledby="tab-distribution" hidden={tab !== 'distribution'}>
                <DistributionView key={buildId} buildId={buildId} build={build} />
              </div>
            )}
            {opened.has('defences') && (
              <div role="tabpanel" id="panel-defences" aria-labelledby="tab-defences" hidden={tab !== 'defences'}>
                <DefenceView key={buildId} buildId={buildId} build={build} />
              </div>
            )}
            {opened.has('mana') && (
              <div role="tabpanel" id="panel-mana" aria-labelledby="tab-mana" hidden={tab !== 'mana'}>
                <ManaView key={buildId} buildId={buildId} build={build} entries={entries} />
              </div>
            )}
            {opened.has('history') && origin.character && (
              <div role="tabpanel" id="panel-history" aria-labelledby="tab-history" hidden={tab !== 'history'}>
                <HistoryView key={origin.character} character={origin.character} openSnapshotId={origin.snapshotId} onOpen={openSnapshot} />
              </div>
            )}
          </div>
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
