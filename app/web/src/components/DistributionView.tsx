import * as Plot from '@observablehq/plot'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  api, type BuildSummary, type ConfigOption, type Distribution, type DistScenario, type Outcome,
} from '../api'
import { formatNumber } from '../format'

// The damage distribution tab. Left: what to look at (skill, scenarios). Right: how each
// scenario compares, the distribution itself, and the rolls a hit is made of. Every number
// is the engine's model or a sample drawn from it (api/distribution.py).

const SAMPLES = 20000
const BINS = 80
const MAX_SCENARIOS = 6

const OUTCOMES: { key: Outcome; label: string; color: string }[] = [
  { key: 'miss', label: 'Miss', color: '#6f6a5e' },
  { key: 'hit', label: 'Non-crit', color: '#3987e5' },
  { key: 'crit', label: 'Crit', color: '#d95926' },
]
const OUTCOME_LABEL = Object.fromEntries(OUTCOMES.map((o) => [o.key, o.label])) as Record<Outcome, string>

interface ScenarioDef {
  id: string
  name: string
  config: Record<string, boolean>
}

const BASELINE: ScenarioDef = { id: 'base', name: 'As configured', config: {} }

// Offered up front. Each flips the build's current setting; the rest are a search away.
const PRESETS: { var: string; defaultValue: boolean; whenOn: string; whenOff: string }[] = [
  { var: 'conditionSurrounded', defaultValue: false, whenOn: 'Surrounded', whenOff: 'Not surrounded' },
  { var: 'companionInPresence', defaultValue: true, whenOn: 'Companion in Presence', whenOff: 'No companion in Presence' },
]

function currentValue(opt: ConfigOption | undefined, fallback = false): boolean {
  return typeof opt?.value === 'boolean' ? opt.value : fallback
}

// A config question ("Are you surrounded?") turned into a scenario name.
function flipName(opt: ConfigOption, to: boolean): string {
  const label = opt.label.replace(/[:?]\s*$/, '')
  return to ? label : `Not: ${label}`
}

interface Props {
  buildId: string
  build: BuildSummary
}

export function DistributionView({ buildId, build }: Props) {
  const skills = useMemo(() => {
    const seen = new Set<string>()
    return build.skills.filter((g) => {
      if (!g.enabled || !g.activeSkill || seen.has(g.activeSkill)) return false
      seen.add(g.activeSkill)
      return true
    })
  }, [build])
  const [skill, setSkill] = useState<string>(() => (skills.find((g) => g.isMain) ?? skills[0])?.activeSkill ?? '')
  const [scenarios, setScenarios] = useState<ScenarioDef[]>([BASELINE])
  const [presetsAdded, setPresetsAdded] = useState(false)
  const [scale, setScale] = useState<'linear' | 'sqrt'>('linear')
  const [focus, setFocus] = useState(0)  // scenario whose rolls are shown
  const [data, setData] = useState<Distribution | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [answered, setAnswered] = useState<string | null>(null)

  // Start with the presets the build actually has, each flipping its current setting.
  useEffect(() => {
    if (presetsAdded) return
    Promise.all(PRESETS.map((p) => api.config(buildId, p.var).then((r) => r.options.find((o) => o.var === p.var))))
      .then((opts) => {
        const added: ScenarioDef[] = []
        opts.forEach((opt, i) => {
          if (!opt) return
          const p = PRESETS[i]
          const now = currentValue(opt, p.defaultValue)
          added.push({ id: p.var, name: now ? p.whenOff : p.whenOn, config: { [p.var]: !now } })
        })
        setScenarios([BASELINE, ...added])
        setPresetsAdded(true)
      })
      .catch(() => setPresetsAdded(true))
  }, [buildId, presetsAdded])

  const request = useMemo(
    () => scenarios.map((s) => (s.id === 'base' ? { name: s.name } : { name: s.name, config: s.config })),
    [scenarios],
  )
  const requestKey = `${skill}|${JSON.stringify(request)}`
  const loading = presetsAdded && answered !== requestKey

  useEffect(() => {
    if (!skill || !presetsAdded) return
    let cancelled = false
    api.distribution(buildId, { skill, scenarios: request, samples: SAMPLES, by_type: false })
      .then((d) => {
        if (cancelled) return
        setData(d)
        setError(null)
        setAnswered(requestKey)
      })
      .catch((err) => {
        if (cancelled) return
        setData(null)
        setError(err instanceof Error ? err.message : String(err))
        setAnswered(requestKey)
      })
    return () => { cancelled = true }
  }, [buildId, build, skill, request, requestKey, presetsAdded])

  async function download() {
    const full = await api.distribution(buildId, { skill, scenarios: request, samples: SAMPLES, by_type: true })
    const url = URL.createObjectURL(new Blob([JSON.stringify(full)], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `distribution-${skill.toLowerCase().replace(/[^a-z0-9]+/g, '-')}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  function addScenario(s: ScenarioDef) {
    setScenarios((prev) => (prev.some((p) => p.id === s.id) || prev.length >= MAX_SCENARIOS ? prev : [...prev, s]))
  }
  function removeScenario(id: string) {
    setScenarios((prev) => prev.filter((p) => p.id !== id))
    setFocus(0)
  }

  const shown = data
  const focused = shown?.scenarios[Math.min(focus, (shown?.scenarios.length ?? 1) - 1)]

  return (
    <div className="dist-view">
      <aside className="dist-rail panel" aria-label="What to show">
        <section>
          <h3>Skill</h3>
          <ul className="skill-picker" role="radiogroup" aria-label="Skill">
            {skills.map((g) => (
              <li key={g.index}>
                <label className={g.activeSkill === skill ? 'selected' : ''}>
                  <input
                    type="radio"
                    name="dist-skill"
                    checked={g.activeSkill === skill}
                    onChange={() => { setSkill(g.activeSkill!); setFocus(0) }}
                  />
                  <span>{g.activeSkill}</span>
                  {g.isMain && <span className="badge">main</span>}
                </label>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h3>Scenarios</h3>
          <p className="muted small-print">Each changes one Configuration setting, for this view only.</p>
          <ul className="scenario-list">
            {scenarios.map((s) => (
              <li key={s.id}>
                <span>{s.name}</span>
                {s.id !== 'base' && (
                  <button className="icon" onClick={() => removeScenario(s.id)} aria-label={`Remove ${s.name}`}>×</button>
                )}
              </li>
            ))}
          </ul>
          {scenarios.length < MAX_SCENARIOS
            ? <ScenarioSearch buildId={buildId} taken={new Set(scenarios.map((s) => s.id))} onAdd={addScenario} />
            : <p className="muted small-print">Up to {MAX_SCENARIOS} scenarios.</p>}
        </section>

        <section>
          <h3>Data</h3>
          <button className="ghost small" onClick={() => void download()} disabled={!shown}>Download the samples</button>
          <p className="muted small-print">
            {SAMPLES.toLocaleString()} sampled attacks per scenario, with outcome, multiplier and damage by type.
          </p>
        </section>
      </aside>

      <div className="dist-main">
        <header className="dist-head">
          <div>
            <h2>{skill || 'No skill'} · damage per attack</h2>
            <p className="muted">
              Every random draw PoB averages, sampled: hit or miss, crit or not, each damage type’s roll,
              lucky rolls, double and triple damage. Dashed lines mark PoB’s average.
            </p>
          </div>
          {loading && <span className="muted nowrap"><span className="spinner inline" aria-hidden /> Sampling…</span>}
        </header>

        {error && <p className="error">{error}</p>}

        {shown && (
          <>
            <ScenarioTable data={shown} focus={focus} onFocus={setFocus} />

            <section className="dist-block" aria-labelledby="dist-chart-title">
              <div className="block-head">
                <h3 id="dist-chart-title">Distribution</h3>
                <div className="segmented" role="group" aria-label="Vertical scale">
                  <button className={scale === 'linear' ? 'on' : ''} aria-pressed={scale === 'linear'} onClick={() => setScale('linear')}>Linear</button>
                  <button className={scale === 'sqrt' ? 'on' : ''} aria-pressed={scale === 'sqrt'} onClick={() => setScale('sqrt')}>Square root (shows crits)</button>
                </div>
              </div>
              <DistChart data={shown} scale={scale} />
            </section>

            {focused && <RollModel scenario={focused} names={shown.scenarios.map((s) => s.name)} focus={focus} onFocus={setFocus} />}
          </>
        )}
      </div>
    </div>
  )
}

// Search the build's Configuration for on/off settings and add one as a scenario.
function ScenarioSearch({ buildId, taken, onAdd }: { buildId: string; taken: Set<string>; onAdd: (s: ScenarioDef) => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ConfigOption[]>([])

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) return
    let cancelled = false
    const t = setTimeout(() => {
      api.config(buildId, q)
        .then((r) => { if (!cancelled) setResults(r.options.filter((o) => o.type === 'check').slice(0, 8)) })
        .catch(() => undefined)
    }, 200)
    return () => { cancelled = true; clearTimeout(t) }
  }, [buildId, query])

  const visible = query.trim().length >= 2 ? results : []
  return (
    <div className="scenario-search">
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Add a scenario: shocked, frenzy, low life…"
        aria-label="Search Configuration settings"
      />
      {visible.length > 0 && (
        <ul>
          {visible.filter((o) => !taken.has(o.var)).map((o) => {
            const to = !currentValue(o)
            return (
              <li key={o.var}>
                <button
                  className="suggestion"
                  onClick={() => { onAdd({ id: o.var, name: flipName(o, to), config: { [o.var]: to } }); setQuery('') }}
                >
                  {flipName(o, to)}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function pctChange(v: number, base: number): string {
  if (base === 0) return '—'
  const d = (v / base - 1) * 100
  if (Math.abs(d) < 0.05) return 'no change'
  return `${d > 0 ? '+' : '−'}${formatNumber(Math.abs(d))}%`
}

// One row per scenario: the numbers a reader compares first.
function ScenarioTable({ data, focus, onFocus }: { data: Distribution; focus: number; onFocus: (i: number) => void }) {
  const base = data.scenarios[0]
  const pct = (v?: number) => (v === undefined ? '—' : `${formatNumber(v * 100)}%`)
  return (
    <section className="dist-block" aria-labelledby="dist-compare">
      <h3 id="dist-compare">Scenarios</h3>
      <div className="table-scroll">
        <table className="dist-compare">
          <thead>
            <tr>
              <th>Scenario</th>
              <th>PoB average</th>
              <th>Median</th><th>Middle 80%</th><th>99th pct</th>
              <th>Miss</th><th>Crit</th><th>DPS</th>
            </tr>
          </thead>
          <tbody>
            {data.scenarios.map((s, i) => {
              const q = s.summary.quantiles
              const delta = i === 0 ? '' : pctChange(s.summary.mean_per_attempt, base.summary.mean_per_attempt)
              return (
                <tr key={s.name} className={i === focus ? 'focused' : ''} onClick={() => onFocus(i)}>
                  <th scope="row">{s.name}</th>
                  <td>
                    <span className="strong">{formatNumber(s.summary.mean_per_attempt)}</span>
                    {delta && <span className={`delta ${delta.startsWith('+') ? 'up' : delta.startsWith('−') ? 'down' : 'muted'}`}>{delta}</span>}
                  </td>
                  <td>{formatNumber(q.p50)}</td>
                  <td>{formatNumber(q.p10)} – {formatNumber(q.p90)}</td>
                  <td>{formatNumber(q.p99)}</td>
                  <td>{pct(s.summary.share.miss)}</td>
                  <td>{pct(s.summary.share.crit)}</td>
                  <td>{formatNumber(s.pob.totalDPS)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

// Width of a chart's container, kept current as it resizes or a hidden tab is shown.
function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => setWidth(Math.round(el.clientWidth))
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

function DistChart({ data, scale }: { data: Distribution; scale: 'linear' | 'sqrt' }) {
  const [ref, width] = useWidth()

  useEffect(() => {
    const el = ref.current
    if (!el || width === 0) return
    const base = data.scenarios[0].summary.mean_per_attempt
    const names = data.scenarios.map((s, i) => {
      if (i === 0) return s.name
      const d = pctChange(s.summary.mean_per_attempt, base)
      return `${s.name} (${d})`
    })
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
      width,
      height: 60 + 165 * data.scenarios.length,
      marginLeft: 48,
      marginRight: 12,
      marginBottom: 44,
      style: { background: 'transparent', color: 'var(--muted)', fontSize: '12px', fontFamily: 'inherit' },
      x: { label: 'Damage per attack →', domain: [0, maxDamage], tickFormat: (d: number) => formatNumber(d), grid: false },
      y: {
        type: scale,
        label: '↑ Share of attacks',
        tickFormat: (d: number) => `${Math.round(d * 1000) / 10}%`,
        ticks: scale === 'sqrt' ? [0, 0.005, 0.02, 0.05, 0.1] : 3,
        grid: true,
      },
      fy: { axis: null, domain: names, padding: 0.3 },
      color: { domain: OUTCOMES.map((o) => o.label), range: OUTCOMES.map((o) => o.color), legend: true },
      marks: [
        Plot.rectY(rows, Plot.binX<Plot.RectYOptions>(
          { y: 'proportion-facet' },
          { x: 'damage', fill: 'outcome', fy: 'scenario', thresholds, insetLeft: 0.5, insetRight: 0.5, tip: true },
        )),
        Plot.ruleY([0], { stroke: 'var(--border)' }),
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
  }, [data, scale, width, ref])

  return <div className="dist-chart" ref={ref} role="img" aria-label={`Damage per attack of ${data.skill}, by scenario`} />
}

// What one hit is made of: the chances, then each damage type's roll range for non-crits
// and crits. Same outcome colours as the distribution.
function RollModel({ scenario, names, focus, onFocus }: {
  scenario: DistScenario; names: string[]; focus: number; onFocus: (i: number) => void
}) {
  const [ref, width] = useWidth()
  const hand = scenario.hands[0]
  const m = hand.model

  useEffect(() => {
    const el = ref.current
    if (!el || width === 0) return
    const rows = m.types.flatMap((t) => (['hit', 'crit'] as const).flatMap((side) => {
      const r = t[side]
      return r ? [{ type: t.type, outcome: OUTCOME_LABEL[side], min: r.min, max: r.max, lucky: r.lucky }] : []
    }))
    const chart = Plot.plot({
      width,
      height: 40 + 54 * m.types.length,
      marginLeft: 96,
      marginRight: 64,
      marginBottom: 44,
      style: { background: 'transparent', color: 'var(--muted)', fontSize: '12px', fontFamily: 'inherit' },
      x: { label: 'Damage roll range (after enemy mitigation) →', tickFormat: (d: number) => formatNumber(d), grid: true, nice: true },
      // One panel per damage type, a lane each for non-crits and crits, so ranges never overlap.
      fy: { label: null, domain: m.types.map((t) => t.type), padding: 0.15 },
      y: { axis: null, domain: ['Non-crit', 'Crit'] },
      color: { domain: ['Non-crit', 'Crit'], range: [OUTCOMES[1].color, OUTCOMES[2].color] },
      marks: [
        Plot.barX(rows, {
          x1: 'min', x2: 'max', y: 'outcome', fy: 'type', fill: 'outcome', insetTop: 2, insetBottom: 2, rx: 3,
          channels: { Min: 'min', Max: 'max' },
          tip: { format: { x1: false, x2: false, y: true, fy: true, fill: false, Min: (d: number) => formatNumber(d), Max: (d: number) => formatNumber(d) } },
        }),
        Plot.text(rows, {
          x: 'max', y: 'outcome', fy: 'type', text: 'outcome', textAnchor: 'start', dx: 6,
          fill: 'var(--muted)', fontSize: 11,
        }),
      ],
    })
    el.replaceChildren(chart)
    return () => { el.replaceChildren() }
  }, [m, width, ref])

  return (
    <section className="dist-block" aria-labelledby="dist-rolls">
      <div className="block-head">
        <h3 id="dist-rolls">What one hit is made of</h3>
        {names.length > 1 && (
          <select value={focus} onChange={(e) => onFocus(Number(e.target.value))} aria-label="Scenario">
            {names.map((n, i) => <option key={n} value={i}>{n}</option>)}
          </select>
        )}
      </div>
      <ul className="chance-chips">
        <li><span>Hit chance</span><strong>{formatNumber(m.hitChance * 100)}%</strong></li>
        <li><span>Crit chance, given a hit</span><strong>{formatNumber(m.critChance * 100)}%</strong></li>
        {m.critMultiplier !== undefined && <li><span>Crit multiplier</span><strong>{formatNumber(m.critMultiplier)}×</strong></li>}
        <li><span>Double damage</span><strong>{formatNumber(m.doubleDamageChance * 100)}%</strong></li>
        <li><span>Triple damage</span><strong>{formatNumber(m.tripleDamageChance * 100)}%</strong></li>
        {scenario.hands.length > 1 && <li><span>Hands</span><strong>{scenario.hands.map((h) => h.hand).join(' / ')}, alternating</strong></li>}
      </ul>
      <div className="dist-chart compact" ref={ref} role="img" aria-label="Damage roll range by type, non-crit and crit" />
      <div className="table-scroll">
        <table className="dist-compare">
          <thead>
            <tr><th>Type</th><th>Non-crit roll</th><th>Crit roll</th><th>Lucky</th><th>Enemy mitigation</th></tr>
          </thead>
          <tbody>
            {m.types.map((t) => (
              <tr key={t.type}>
                <th scope="row">{t.type}</th>
                <td>{t.hit ? `${formatNumber(t.hit.min)} – ${formatNumber(t.hit.max)}` : '—'}</td>
                <td>{t.crit ? `${formatNumber(t.crit.min)} – ${formatNumber(t.crit.max)}` : '—'}</td>
                <td>{t.hit && t.hit.lucky > 0 ? `${formatNumber(t.hit.lucky * 100)}%` : '—'}</td>
                <td title="Damage dealt after the enemy's resistance or armour, as a multiplier">
                  {t.hit ? `×${t.hit.mitigation.toFixed(2)}` : '—'}
                  {t.hit && t.crit && Math.abs(t.hit.mitigation - t.crit.mitigation) > 0.005 ? ` (crits ×${t.crit.mitigation.toFixed(2)})` : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
