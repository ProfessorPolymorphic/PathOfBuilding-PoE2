import * as Plot from '@observablehq/plot'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError, type BuildSummary, type ConfigOption, type Distribution, type Outcome, type ScenarioRequest } from '../api'
import { formatNumber } from '../format'

// Damage of one attack as a distribution, under scenarios that override Configuration
// settings for this calculation only. A first sketch to design from: small multiples (one
// panel per scenario) on a shared damage axis, stacked by outcome.

const SAMPLES = 20000
const BINS = 80

const OUTCOMES: { key: Outcome; label: string; color: string }[] = [
  { key: 'miss', label: 'Miss', color: '#6f6a5e' },
  { key: 'hit', label: 'Non-crit', color: '#3987e5' },
  { key: 'crit', label: 'Crit', color: '#d95926' },
]
const OUTCOME_LABEL = Object.fromEntries(OUTCOMES.map((o) => [o.key, o.label])) as Record<Outcome, string>

// Configuration settings offered as scenarios. Each flips the build's current setting.
interface Preset {
  var: string
  defaultValue: boolean
  whenOn: string   // scenario name when it turns the setting on
  whenOff: string  // ...and when it turns it off
}
const PRESETS: Preset[] = [
  { var: 'conditionSurrounded', defaultValue: false, whenOn: 'Surrounded', whenOff: 'Not surrounded' },
  { var: 'companionInPresence', defaultValue: true, whenOn: 'Companion in Presence', whenOff: 'No companion in Presence' },
]

interface Props {
  buildId: string
  build: BuildSummary
}

export function HitDistribution({ buildId, build }: Props) {
  const skills = useMemo(() => {
    const seen = new Set<string>()
    return build.skills.filter((g) => {
      if (!g.enabled || !g.activeSkill || seen.has(g.activeSkill)) return false
      seen.add(g.activeSkill)
      return true
    })
  }, [build])
  const main = skills.find((g) => g.isMain) ?? skills[0]
  const [skill, setSkill] = useState<string>(main?.activeSkill ?? '')
  const [config, setConfig] = useState<Record<string, ConfigOption>>({})
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(PRESETS.map((p) => p.var)))
  const [data, setData] = useState<Distribution | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Which request the shown data/error answers, so a newer request shows as loading.
  const [answered, setAnswered] = useState<string | null>(null)

  // Current values of the preset settings, so each scenario flips what the build has now.
  useEffect(() => {
    Promise.all(PRESETS.map((p) => api.config(buildId, p.var)))
      .then((results) => {
        const found: Record<string, ConfigOption> = {}
        results.forEach((r, i) => {
          const opt = r.options.find((o) => o.var === PRESETS[i].var)
          if (opt) found[opt.var] = opt
        })
        setConfig(found)
      })
      .catch(() => undefined)
  }, [buildId, build])

  const scenarios: ScenarioRequest[] = useMemo(() => {
    const list: ScenarioRequest[] = [{ name: 'As configured' }]
    for (const p of PRESETS) {
      if (!chosen.has(p.var) || !config[p.var]) continue
      const current = typeof config[p.var].value === 'boolean' ? (config[p.var].value as boolean) : p.defaultValue
      list.push({ name: current ? p.whenOff : p.whenOn, config: { [p.var]: !current } })
    }
    return list
  }, [chosen, config])
  const requestKey = `${skill}|${JSON.stringify(scenarios)}`
  const loading = answered !== requestKey

  useEffect(() => {
    if (!skill) return
    let cancelled = false
    api.distribution(buildId, { skill, scenarios, samples: SAMPLES, by_type: false })
      .then((d) => {
        if (cancelled) return
        setData(d)
        setError(null)
        setAnswered(requestKey)
      })
      .catch((err) => {
        if (cancelled) return
        setData(null)
        setError(err instanceof ApiError || err instanceof Error ? err.message : String(err))
        setAnswered(requestKey)
      })
    return () => { cancelled = true }
  }, [buildId, build, skill, scenarios, requestKey])

  async function download() {
    const full = await api.distribution(buildId, { skill, scenarios, samples: SAMPLES, by_type: true })
    const blob = new Blob([JSON.stringify(full)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `distribution-${skill.toLowerCase().replace(/[^a-z0-9]+/g, '-')}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <section className="dash-section dash-wide" aria-labelledby="hit-dist">
      <header className="dash-head">
        <h2 id="hit-dist">Damage per attack</h2>
        <p className="muted">
          Every random draw PoB averages, sampled {SAMPLES.toLocaleString()} times: hit or miss,
          crit or not, each damage type’s roll, lucky rolls, double and triple damage. The dashed
          line is PoB’s average. Scenarios change Configuration for this chart only.
        </p>
      </header>

      <div className="dist-controls">
        <label>
          <span className="muted">Skill </span>
          <select value={skill} onChange={(e) => setSkill(e.target.value)}>
            {skills.map((g) => <option key={g.index} value={g.activeSkill}>{g.activeSkill}</option>)}
          </select>
        </label>
        {PRESETS.filter((p) => config[p.var]).map((p) => {
          const current = typeof config[p.var].value === 'boolean' ? (config[p.var].value as boolean) : p.defaultValue
          return (
            <label key={p.var} className="dist-toggle">
              <input
                type="checkbox"
                checked={chosen.has(p.var)}
                onChange={(e) => {
                  const next = new Set(chosen)
                  if (e.target.checked) next.add(p.var)
                  else next.delete(p.var)
                  setChosen(next)
                }}
              />
              {current ? p.whenOff : p.whenOn}
            </label>
          )
        })}
        {data && <button className="ghost small" onClick={() => void download()}>Download the samples</button>}
        {loading && <span className="muted"><span className="spinner inline" aria-hidden /> Sampling…</span>}
      </div>

      {error && <p className="error">{error}</p>}
      {data && <DistChart data={data} />}
      {data && <DistTable data={data} />}
    </section>
  )
}

function sameAsFirst(data: Distribution, i: number): boolean {
  if (i === 0) return false
  return Math.abs(data.scenarios[i].pob.averageDamage - data.scenarios[0].pob.averageDamage) < 1e-6
}

function DistChart({ data }: { data: Distribution }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const names = data.scenarios.map((s, i) => (sameAsFirst(data, i) ? `${s.name} (no change)` : s.name))
    const rows: { scenario: string; damage: number; outcome: string }[] = []
    data.scenarios.forEach((s, i) => {
      const { damage, outcome } = s.samples
      for (let j = 0; j < damage.length; j++) rows.push({ scenario: names[i], damage: damage[j], outcome: OUTCOME_LABEL[outcome[j]] })
    })
    const maxDamage = Math.max(...data.scenarios.map((s) => s.summary.max))
    const step = maxDamage / BINS
    const thresholds = Array.from({ length: BINS + 1 }, (_, i) => i * step)
    const means = data.scenarios.map((s, i) => ({ scenario: names[i], mean: s.summary.mean_per_attempt }))

    const chart = Plot.plot({
      width: Math.max(320, el.clientWidth),
      height: 70 + 150 * data.scenarios.length,
      marginLeft: 48,
      marginRight: 16,
      style: { background: 'transparent', color: 'var(--muted)', fontSize: '12px', fontFamily: 'inherit' },
      x: { label: 'Damage per attack →', domain: [0, maxDamage], tickFormat: (d: number) => formatNumber(d), grid: false },
      y: { label: '↑ Share of attacks', tickFormat: (d: number) => `${Math.round(d * 1000) / 10}%`, ticks: 3, grid: true },
      fy: { axis: null, domain: names, padding: 0.3 },
      color: { domain: OUTCOMES.map((o) => o.label), range: OUTCOMES.map((o) => o.color), legend: true },
      marks: [
        Plot.rectY(rows, Plot.binX<Plot.RectYOptions>(
          { y: 'proportion-facet' },
          { x: 'damage', fill: 'outcome', fy: 'scenario', thresholds, insetLeft: 0.5, insetRight: 0.5, tip: true },
        )),
        Plot.ruleY([0], { stroke: 'var(--border)' }),
        // Scenario names inside each panel, so long names never clip at the edge.
        Plot.text(names, {
          fy: (d: string) => d, text: (d: string) => d, frameAnchor: 'top-right', dy: -4,
          fill: 'var(--text)', fontWeight: 600, fontSize: 12.5,
        }),
        Plot.ruleX(means, { x: 'mean', fy: 'scenario', stroke: 'var(--text)', strokeDasharray: '4 3', strokeWidth: 1.5 }),
        Plot.text(means, {
          x: 'mean', fy: 'scenario', frameAnchor: 'top', dy: 2, dx: 6, textAnchor: 'start',
          text: (d: { mean: number }) => `PoB average ${formatNumber(d.mean)}`, fill: 'var(--text)',
        }),
      ],
    })
    el.replaceChildren(chart)
    return () => { el.replaceChildren() }
  }, [data])

  return <div className="dist-chart" ref={ref} role="img" aria-label={`Damage per attack of ${data.skill}, by scenario`} />
}

function DistTable({ data }: { data: Distribution }) {
  const pct = (v?: number) => (v === undefined ? '—' : `${formatNumber(v * 100)}%`)
  return (
    <details className="dist-table">
      <summary><h3>As a table</h3></summary>
      <table>
        <thead>
          <tr>
            <th>Scenario</th><th>PoB average</th><th>Median</th><th>10th pct</th><th>90th pct</th><th>99th pct</th>
            <th>Highest sampled</th><th>Miss</th><th>Crit</th><th>DPS (PoB)</th>
          </tr>
        </thead>
        <tbody>
          {data.scenarios.map((s, i) => (
            <tr key={s.name}>
              <th scope="row">{s.name}{sameAsFirst(data, i) ? ' (no change)' : ''}</th>
              <td>{formatNumber(s.summary.mean_per_attempt)}</td>
              <td>{formatNumber(s.summary.quantiles.p50)}</td>
              <td>{formatNumber(s.summary.quantiles.p10)}</td>
              <td>{formatNumber(s.summary.quantiles.p90)}</td>
              <td>{formatNumber(s.summary.quantiles.p99)}</td>
              <td>{formatNumber(s.summary.max)}</td>
              <td>{pct(s.summary.share.miss)}</td>
              <td>{pct(s.summary.share.crit)}</td>
              <td>{formatNumber(s.pob.totalDPS)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}
