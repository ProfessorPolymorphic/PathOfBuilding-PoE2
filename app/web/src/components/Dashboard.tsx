import { useEffect, useState } from 'react'
import { api, ApiError, type BuildSummary, type Card, type Dashboard as DashboardData, type NodeCandidate } from '../api'
import { BuildDetails } from './BuildDetails'
import { ChangeCard } from './ChangeCard'
import { HitDistribution } from './HitDistribution'
import { StatPanel } from './StatPanel'

// What pasting a build code fills in: the build's core stats, then the best reachable
// passives by damage and by defence, each as a change preview card. More views (weak spots,
// the damage/defence scatter, support gems, pathing, progress) slot in as sections here.

const TOP_N = 3

interface Props {
  buildId: string
  build: BuildSummary
  onBuildLost: () => void
}

function cellDelta(card: Card, key: string): number {
  for (const g of card.core) for (const r of g.rows) for (const c of r.cells) {
    if (c.key === key) return c.delta ?? 0
  }
  return 0
}

function dpsDelta(card: Card): number {
  const cell = card.core.find((g) => g.group === 'Damage')?.rows[0]?.cells[0]
  return cell?.delta ?? 0
}

function top(nodes: NodeCandidate[], by: (n: NodeCandidate) => number): NodeCandidate[] {
  return nodes.filter((n) => by(n) > 0).sort((a, b) => by(b) - by(a)).slice(0, TOP_N)
}

export function Dashboard({ buildId, build, onBuildLost }: Props) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Recompute whenever the build changes (the server caches until then).
  useEffect(() => {
    let cancelled = false
    api.dashboard(buildId)
      .then((d) => {
        if (cancelled) return
        setData(d)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) onBuildLost()
        else setError(err instanceof Error ? err.message : String(err))
      })
    return () => { cancelled = true }
  }, [buildId, build, onBuildLost])

  const nodes = data?.nodes.candidates ?? []
  const byDamage = top(nodes, (n) => dpsDelta(n.card))
  const byDefence = top(nodes, (n) => cellDelta(n.card, 'TotalEHP'))

  return (
    <div className="dashboard">
      <section className="dash-section dash-build">
        {data ? (
          <ChangeCard
            card={data.baseline}
            mode="baseline"
            title={<>Your build</>}
            subtitle={`Level ${build.level} ${build.ascendancy ?? build.class}${build.mainSkill ? ` · ${build.mainSkill}` : ''}`}
          />
        ) : (
          <div className="change-card placeholder" aria-busy>{error ? <p className="error">{error}</p> : <p className="muted">Testing your build…</p>}</div>
        )}
        <details className="panel dash-details">
          <summary><h3>All stats, skills and gear</h3></summary>
          <div className="dash-details-body">
            <StatPanel stats={build.stats} />
            <BuildDetails build={build} />
          </div>
        </details>
      </section>

      <section className="dash-section" aria-labelledby="next-passive">
        <header className="dash-head">
          <h2 id="next-passive">Next passive</h2>
          {data && (
            <p className="muted">
              Every notable and keystone within {data.nodes.max_cost} points, tested one at a time
              with the path to it ({data.nodes.evaluated} tested).{' '}
              <a href={api.dashboardDownloadUrl(buildId)} download>Download the data</a>
            </p>
          )}
        </header>
        {!data && !error && <p className="muted"><span className="spinner inline" aria-hidden /> Testing reachable passives in the engine…</p>}
        {data && (
          <div className="dash-columns">
            <NodeColumn title="Most damage" nodes={byDamage} empty="No reachable passive adds damage." />
            <NodeColumn title="Most effective HP" nodes={byDefence} empty="No reachable passive adds effective HP." />
          </div>
        )}
      </section>

      <HitDistribution buildId={buildId} build={build} />
    </div>
  )
}

function NodeColumn({ title, nodes, empty }: { title: string; nodes: NodeCandidate[]; empty: string }) {
  return (
    <div className="dash-column">
      <h3>{title}</h3>
      {nodes.length === 0 && <p className="muted">{empty}</p>}
      <div className="card-stack">
        {nodes.map((n) => (
          <ChangeCard
            key={n.id}
            card={n.card}
            title={n.name}
            subtitle={`${n.type} · ${n.cost} ${n.cost === 1 ? 'point' : 'points'}`}
          >
            <details className="cc-more">
              <summary>What it does{n.path.length > 1 ? ' and the path' : ''}</summary>
              <ul className="cc-stats">{n.stats.map((s, i) => <li key={i}>{s}</li>)}</ul>
              {n.path.length > 1 && <p className="cc-path">{n.path.join(' → ')}</p>}
            </details>
          </ChangeCard>
        ))}
      </div>
    </div>
  )
}
