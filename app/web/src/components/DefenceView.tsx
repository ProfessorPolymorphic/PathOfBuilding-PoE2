import * as Plot from '@observablehq/plot'
import { useEffect, useMemo, useState } from 'react'
import { api, type BuildSummary, type Defence, type Sweep, type SweepStep } from '../api'
import { DAMAGE_COLORS, OUTCOME_COLORS } from '../colors'
import { formatNumber } from '../format'
import { useWidth } from '../useWidth'
import { mainSkill, useSkills } from '../skills'
import { SkillPicker } from './SkillPicker'

// The "Against enemy defences" tab: one skill's damage per attack as a density map over one
// enemy defence (armour, evasion, a resistance, ...). Each row is PoB's own calculation at
// that defence value, sampled (POST /builds/{id}/sweep); everything else stays as configured.

const STEPS = 25
const SAMPLES = 8000
const BINS = 160
const RIGHT = 180     // width of the average-per-attack panel
const MAIN_H = 380
const TOP_H = 96

// Brightness ramp for the map: the panel colour up to the accent, then pale.
const RAMP = ['#121917', '#1d4639', OUTCOME_COLORS.crit, '#e9fff6']
const C = { text: '#e7edeb', muted: '#98a5a2', accent: OUTCOME_COLORS.crit, border: '#293330' }
const STYLE = { background: 'transparent', color: 'var(--muted)', fontSize: '12px', fontFamily: 'inherit' }

interface Props {
  buildId: string
  build: BuildSummary
}

// Defences grouped for the picker; resistances carry their damage type's colour.
const GROUPS: { title: string; vars: string[] }[] = [
  { title: 'Physical', vars: ['enemyArmour', 'enemyPhysicalReduction'] },
  { title: 'Avoidance', vars: ['enemyEvasion', 'enemyBlockChance'] },
  { title: 'Resistances', vars: ['enemyFireResist', 'enemyColdResist', 'enemyLightningResist', 'enemyChaosResist'] },
  { title: 'Other', vars: ['enemyLevel'] },
]

function withUnit(v: number, unit: string): string {
  return `${formatNumber(v)}${unit}`
}

function shortLabel(d: Defence): string {
  return d.label.replace(/^Enemy /, '').replace(/^./, (c) => c.toUpperCase())
}

export function DefenceView({ buildId, build }: Props) {
  const skills = useSkills(build)
  const [skill, setSkill] = useState(() => mainSkill(skills))
  const [defences, setDefences] = useState<Defence[]>([])
  const [defence, setDefence] = useState('enemyArmour')
  const [range, setRange] = useState<Record<string, [number, number]>>({})
  const [scale, setScale] = useState<'linear' | 'sqrt'>('linear')
  const [data, setData] = useState<Sweep | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [answered, setAnswered] = useState<string | null>(null)

  useEffect(() => {
    api.defences().then(setDefences).catch(() => setDefences([]))
  }, [])

  const meta = defences.find((d) => d.var === defence)
  const [lo, hi] = range[defence] ?? meta?.range ?? [0, 0]
  const valid = Boolean(meta) && hi > lo
  const requestKey = `${skill}|${defence}|${lo}|${hi}`
  const loading = valid && answered !== requestKey

  useEffect(() => {
    if (!skill || !valid) return
    let cancelled = false
    // Short delay so typing a range doesn't send a sweep per keystroke.
    const timer = setTimeout(() => {
      api.sweep(buildId, { skill, defence, lo, hi, steps: STEPS, samples: SAMPLES, bins: BINS })
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
    }, 250)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [buildId, build, skill, defence, lo, hi, valid, requestKey])

  function setBound(which: 0 | 1, value: string) {
    const n = Number(value)
    if (!Number.isFinite(n) || !meta) return
    const next: [number, number] = [lo, hi]
    next[which] = n
    setRange((r) => ({ ...r, [defence]: next }))
  }

  function download() {
    if (!data) return
    const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `sweep-${data.skill.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-${data.defence}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const shown = data && data.defence === defence ? data : null

  return (
    <div className="dist-view">
      <aside className="dist-rail panel" aria-label="What to show">
        <section>
          <h3>Skill</h3>
          <SkillPicker skills={skills} value={skill} name="defence-skill" onChange={setSkill} />
        </section>

        <section>
          <h3>Enemy defence</h3>
          {GROUPS.map((g) => (
            <div key={g.title} className="defence-group">
              <p className="muted small-print">{g.title}</p>
              <ul className="skill-picker" role="radiogroup" aria-label={g.title}>
                {g.vars.map((v) => {
                  const d = defences.find((x) => x.var === v)
                  if (!d) return null
                  const type = d.var.match(/^enemy(Fire|Cold|Lightning|Chaos)Resist$/)?.[1]
                  return (
                    <li key={v}>
                      <label className={v === defence ? 'selected' : ''}>
                        <input type="radio" name="defence" checked={v === defence} onChange={() => setDefence(v)} />
                        {type && <span className="type-key" style={{ background: DAMAGE_COLORS[type] }} aria-hidden />}
                        <span>{shortLabel(d)}</span>
                      </label>
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </section>

        {meta && (
          <section>
            <h3>Range</h3>
            <div className="range-inputs">
              <label>From<input type="number" value={lo} onChange={(e) => setBound(0, e.target.value)} /></label>
              <label>To<input type="number" value={hi} onChange={(e) => setBound(1, e.target.value)} /></label>
            </div>
            {!valid && <p className="error small-print">“To” must be above “From”.</p>}
            {range[defence] && (
              <button className="ghost small" onClick={() => setRange((r) => { const n = { ...r }; delete n[defence]; return n })}>
                Reset range
              </button>
            )}
            <p className="muted small-print">{STEPS} values in this range, plus your config’s own value.</p>
          </section>
        )}

        <section>
          <h3>Data</h3>
          <button className="ghost small" onClick={download} disabled={!shown}>Download the sweep</button>
          <p className="muted small-print">
            Every row: PoB’s averages, the summary and the binned damage ({SAMPLES.toLocaleString()} sampled attacks per row).
          </p>
        </section>
      </aside>

      <div className="dist-main">
        <header className="dist-head">
          <div>
            <h2>{skill || 'No skill'} · against {meta ? meta.label.toLowerCase() : 'enemy defences'}</h2>
            <p className="muted">
              Each row is PoB’s calculation at that value, everything else as configured, sampled like the
              Damage distribution tab. Brighter means more attacks land there.
            </p>
          </div>
          {loading && <span className="muted nowrap"><span className="spinner inline" aria-hidden /> Calculating…</span>}
        </header>

        {error && <p className="error">{error}</p>}
        {shown && <SweepSummary data={shown} />}

        {shown && (
          <section className="dist-block" aria-labelledby="defence-map-title">
            <div className="block-head">
              <h3 id="defence-map-title">Damage per attack by {shortLabel(meta!).toLowerCase()}</h3>
              <div className="segmented" role="group" aria-label="Brightness scale">
                <button className={scale === 'linear' ? 'on' : ''} aria-pressed={scale === 'linear'} onClick={() => setScale('linear')}>Linear</button>
                <button className={scale === 'sqrt' ? 'on' : ''} aria-pressed={scale === 'sqrt'} onClick={() => setScale('sqrt')}>Square root (shows crits)</button>
              </div>
            </div>
            <DefenceMap data={shown} scale={scale} />
          </section>
        )}
      </div>
    </div>
  )
}

function pct(v: number): string {
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${formatNumber(Math.abs(v) * 100)}%`
}

// One sentence of what the sweep shows: the average at both ends and at your config.
function SweepSummary({ data }: { data: Sweep }) {
  const rows = data.steps.filter((s) => s.summary)
  if (rows.length < 2) return null
  const first = rows[0], last = rows[rows.length - 1]
  const current = data.steps.find((s) => data.current !== null && s.value === Math.round(data.current))
  const a = first.summary!.mean_per_attempt, b = last.summary!.mean_per_attempt
  const missA = first.summary!.share.miss ?? 0, missB = last.summary!.share.miss ?? 0
  return (
    <p className="sweep-summary">
      From {withUnit(first.value, data.unit)} to {withUnit(last.value, data.unit)}, the average per attack
      goes from <strong>{formatNumber(a)}</strong> to <strong>{formatNumber(b)}</strong> ({pct(b / a - 1)})
      {Math.abs(missB - missA) > 0.005 && <>, and misses from {formatNumber(missA * 100)}% to {formatNumber(missB * 100)}%</>}.
      {current?.summary && <> At your config ({withUnit(current.value, data.unit)}) it’s <strong>{formatNumber(current.summary.mean_per_attempt)}</strong>.</>}
    </p>
  )
}

// Gaussian smoothing along the damage axis, to take the sampling noise out of each row.
function smooth(row: number[], sigma: number): number[] {
  const r = Math.ceil(sigma * 3)
  const k = Array.from({ length: 2 * r + 1 }, (_, i) => Math.exp(-0.5 * ((i - r) / sigma) ** 2))
  const norm = k.reduce((s, v) => s + v, 0)
  return row.map((_, i) => {
    let sum = 0
    for (let j = -r; j <= r; j++) {
      const v = row[i + j]
      if (v !== undefined) sum += v * k[j + r]
    }
    return sum / norm
  })
}

// Density map (damage × defence) with two marginals: on top, the damage distribution at your
// config and at the row under the pointer; on the right, the average per attack by defence.
function DefenceMap({ data, scale }: { data: Sweep; scale: 'linear' | 'sqrt' }) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<SweepStep | null>(null)

  const m = useMemo(() => {
    const per100 = 100 / data.bin_width
    const density = (s: SweepStep) => smooth(s.hit.map((h, i) => (h + s.crit[i]) * per100), 1.2)
    const grid = data.steps.filter((s) => s.on_grid)
    const values = grid.flatMap(density)
    const dy = (grid[grid.length - 1].value - grid[0].value) / Math.max(1, grid.length - 1)
    const current = data.steps.find((s) => data.current !== null && s.value === Math.round(data.current)) ?? null
    const xmax = data.bin_width * data.bins
    const xs = Array.from({ length: data.bins }, (_, i) => (i + 0.5) * data.bin_width)
    const avg = data.steps.filter((s) => s.summary).map((s) => ({ value: s.value, avg: s.summary!.mean_per_attempt, miss: s.summary!.share.miss ?? 0, step: s }))
    return { density, grid, values, dy, current, xmax, xs, avg, peak: Math.max(...values) }
  }, [data])

  const yDomain: [number, number] = [m.grid[0].value - m.dy / 2, m.grid[m.grid.length - 1].value + m.dy / 2]
  const unit = data.unit
  const fmtY = (d: number) => withUnit(d, unit)

  // Main map and right marginal: redrawn when the data, scale or width change, not on hover.
  useEffect(() => {
    const el = ref.current?.querySelector<HTMLDivElement>('.dm-main')
    const right = ref.current?.querySelector<HTMLDivElement>('.dm-right')
    if (!el || !right || width === 0) return
    const mainW = width - RIGHT
    const colorScale = {
      type: scale, domain: [0, m.peak], range: RAMP, interpolate: 'lab' as const,
      label: 'Share of attacks per 100 damage', tickFormat: (d: number) => `${formatNumber(d * 100)}%`,
    }
    const map = Plot.plot({
      width: mainW,
      height: MAIN_H,
      marginLeft: 56,
      marginRight: 10,
      marginBottom: 40,
      style: STYLE,
      x: { domain: [0, m.xmax], label: 'Damage per attack →', tickFormat: (d: number) => formatNumber(d) },
      y: { domain: yDomain, label: `↑ ${data.label}`, tickFormat: fmtY },
      color: colorScale,
      marks: [
        Plot.raster(m.values, {
          width: data.bins, height: m.grid.length, x1: 0, x2: m.xmax, y1: yDomain[0], y2: yDomain[1],
          imageRendering: 'auto',
        }),
        Plot.lineY(m.avg, { x: 'avg', y: 'value', stroke: C.text, strokeDasharray: '4 3', strokeWidth: 1.5 }),
        ...(m.current ? [
          Plot.ruleY([m.current.value], { stroke: C.accent, strokeWidth: 1 }),
          Plot.text([m.current.value], { y: (d: number) => d, frameAnchor: 'right', dy: -8, text: () => 'your config', fill: C.accent }),
        ] : []),
        // Pointer: the nearest row, with its numbers.
        Plot.ruleY(m.avg, Plot.pointerY({ y: 'value', stroke: C.text, strokeOpacity: 0.55 })),
        Plot.tip(m.avg, Plot.pointerY({
          y: 'value', x: 'avg', anchor: 'left', fill: '#1a2221', stroke: C.border,
          title: (d: { value: number; avg: number; miss: number; step: SweepStep }) => {
            const q = d.step.summary!.quantiles
            return [
              `${data.label}: ${fmtY(d.value)}`,
              `Average ${formatNumber(d.avg)} · median ${formatNumber(q.p50)}`,
              `Middle 80%: ${formatNumber(q.p10)} – ${formatNumber(q.p90)}`,
              `Miss ${formatNumber(d.miss * 100)}% · crit ${formatNumber((d.step.summary!.share.crit ?? 0) * 100)}%`,
            ].join('\n')
          },
        })),
      ],
    })
    map.addEventListener('input', () => {
      const v = (map as unknown as { value: { step: SweepStep } | null }).value
      setHover(v ? v.step : null)
    })
    const avgMax = Math.max(...m.avg.map((d) => d.avg)) * 1.12
    const ends = [m.avg[0], m.avg[m.avg.length - 1]]
    const missMoves = Math.abs(ends[1].miss - ends[0].miss) > 0.005
    const side = Plot.plot({
      width: RIGHT,
      height: MAIN_H,
      marginLeft: 8,
      marginRight: 64,
      marginBottom: 40,
      style: STYLE,
      x: { domain: [0, avgMax], label: 'Average →', ticks: 2, tickFormat: (d: number) => formatNumber(d) },
      y: { domain: yDomain, axis: 'right', label: null, tickFormat: fmtY },
      marks: [
        Plot.areaX(m.avg, { y: 'value', x: 'avg', fill: C.text, fillOpacity: 0.1 }),
        Plot.lineX(m.avg, { y: 'value', x: 'avg', stroke: C.text, strokeWidth: 1.5 }),
        ...(m.current?.summary ? [Plot.dot([m.current], { y: 'value', x: (s: SweepStep) => s.summary!.mean_per_attempt, fill: C.accent, r: 3.5 })] : []),
        ...ends.map((end, i) => Plot.text([end], {
          y: 'value', x: 0, dx: 4, dy: i === 0 ? -14 : 14, textAnchor: 'start', fill: C.text, lineHeight: 1.15,
          // Miss share only when the defence changes it (evasion, block).
          text: (d: { avg: number; miss: number }) => formatNumber(d.avg) + (missMoves ? `\nmiss ${formatNumber(d.miss * 100)}%` : ''),
        })),
      ],
    })
    const legend = ref.current?.querySelector<HTMLDivElement>('.dm-legend')
    legend?.replaceChildren(Plot.legend({ color: colorScale, width: 260, style: STYLE }))
    el.replaceChildren(map)
    right.replaceChildren(side)
    return () => { el.replaceChildren(); right.replaceChildren(); legend?.replaceChildren() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [m, data, scale, width])

  // Top marginal: your config, and the row under the pointer.
  useEffect(() => {
    const el = ref.current?.querySelector<HTMLDivElement>('.dm-top')
    if (!el || width === 0) return
    const rows: { x: number; y: number; which: string }[] = []
    const add = (s: SweepStep | null, which: string) => {
      if (!s) return
      m.density(s).forEach((y, i) => rows.push({ x: m.xs[i], y, which }))
    }
    add(m.current, 'Your config')
    if (hover && hover !== m.current) add(hover, `${data.label} ${fmtY(hover.value)}`)
    const labels = [...new Set(rows.map((r) => r.which))]
    const top = Plot.plot({
      width: width - RIGHT,
      height: TOP_H,
      marginLeft: 56,
      marginRight: 10,
      marginBottom: 6,
      marginTop: 18,
      style: STYLE,
      x: { domain: [0, m.xmax], axis: null },
      y: { axis: null, domain: [0, m.peak * 1.05] },
      color: { domain: labels, range: [C.accent, C.text] },
      marks: [
        Plot.areaY(rows, { x: 'x', y: 'y', z: 'which', fill: 'which', fillOpacity: 0.18, curve: 'basis' }),
        Plot.lineY(rows, { x: 'x', y: 'y', z: 'which', stroke: 'which', strokeWidth: 1.5, curve: 'basis' }),
        Plot.ruleY([0], { stroke: C.border }),
        ...labels.map((label, i) => Plot.text([label], {
          frameAnchor: 'top-right', dy: i * 15 - 14, text: (d: string) => d, fill: (d: string) => d, fontWeight: 600,
        })),
      ],
    })
    el.replaceChildren(top)
    return () => { el.replaceChildren() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [m, hover, width])

  return (
    <div className="defence-map" ref={ref}>
      <div className="dm-legend" />
      <div className="dm-top" aria-hidden />
      <div className="dm-row">
        <div className="dm-main" role="img" aria-label={`Damage per attack of ${data.skill} by ${data.label.toLowerCase()}`} />
        <div className="dm-right" role="img" aria-label={`Average damage per attack by ${data.label.toLowerCase()}`} />
      </div>
    </div>
  )
}
