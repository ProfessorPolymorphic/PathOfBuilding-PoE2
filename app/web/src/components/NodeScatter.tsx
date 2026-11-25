import * as Plot from '@observablehq/plot'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { Card, NodeCandidate } from '../api'
import { formatNumber } from '../format'
import { useWidth } from '../useWidth'
import { NodeCard } from './NodeCard'

// Next passive scatter (roadmap 3): every reachable notable/keystone as damage change against
// a chosen defence change, from the change cards the dashboard already computed. Passives
// that change only one side are dodged into a strip along that axis (they'd otherwise stack
// on it); far outliers are pinned to the edge and listed with their real values. Verdigris
// marks the passives no other beats on both axes. Hover shows the passive's card; click pins.

const C = { text: '#e7edeb', muted: '#98a5a2', accent: '#60d3ac', border: '#293330', axis: '#3c4844', panel: '#121917' }
const STYLE = { background: 'transparent', color: 'var(--muted)', fontSize: '11px', fontFamily: 'inherit' }
const STRIP = 140        // left strip width
const MAIN_H = 300
const BOTTOM_H = 120
const EPS = 0.005        // % change below this counts as no change
const R = { domain: [1, 5], range: [3, 7] }

const MAX_HIT_TYPES = ['Physical', 'Fire', 'Cold', 'Lightning', 'Chaos']

interface Metric { id: string; label: string; value: (cells: Map<string, Cell>, side: 'before' | 'after') => number | null }
type Cell = { before?: number | string; after?: number | string }

// Lower-case the first letter only, so "Effective HP" reads "effective HP".
const lower = (s: string) => s.replace(/^./, (c) => c.toLowerCase())
const num = (v: unknown) => (typeof v === 'number' ? v : null)
const cellValue = (key: string) => (cells: Map<string, Cell>, side: 'before' | 'after') => num(cells.get(key)?.[side])

const METRICS: Metric[] = [
  { id: 'TotalEHP', label: 'Effective HP', value: cellValue('TotalEHP') },
  {
    id: 'lowestMaxHit', label: 'Lowest max hit',
    value: (cells, side) => {
      const v = MAX_HIT_TYPES.map((t) => num(cells.get(`${t}MaximumHitTaken`)?.[side])).filter((x): x is number => x !== null)
      return v.length ? Math.min(...v) : null
    },
  },
  ...MAX_HIT_TYPES.map((t) => ({ id: `${t}MaximumHitTaken`, label: `${t} max hit`, value: cellValue(`${t}MaximumHitTaken`) })),
  { id: 'Life', label: 'Life', value: cellValue('Life') },
  { id: 'EnergyShield', label: 'Energy shield', value: cellValue('EnergyShield') },
]

function cellsOf(card: Card): Map<string, Cell> {
  const m = new Map<string, Cell>()
  for (const g of card.core) for (const r of g.rows) for (const c of r.cells) m.set(c.key, c)
  return m
}

function pctChange(before: number | null, after: number | null): number {
  if (before === null || after === null || before === 0) return 0
  return (after / before - 1) * 100
}

interface Point { node: NodeCandidate; x: number; y: number; frontier: boolean; key: boolean }

// A domain that leaves out far outliers: anything beyond ~2x the 90th percentile of its side.
// Losses get at most a third of the room gains get, since a passive that costs this stat is
// rarely the pick; the ones beyond are pinned at the edge and listed.
function robustDomain(values: number[]): [number, number] {
  const side = (vs: number[]) => {
    if (!vs.length) return 0
    const s = [...vs].sort((a, b) => a - b)
    const q90 = s[Math.floor(0.9 * (s.length - 1))]
    return Math.max(...s.filter((v) => v <= Math.max(q90 * 2.2, s[0])))
  }
  const hi = side(values.filter((v) => v > EPS))
  let lo = side(values.filter((v) => v < -EPS).map((v) => -v))
  if (hi > 0) lo = Math.min(lo, hi / 3)
  const span = Math.max(hi, lo, 1)
  return [-(lo || span * 0.12) * 1.1, (hi || span * 0.12) * 1.1]
}

export function NodeScatter({ nodes }: { nodes: NodeCandidate[] }) {
  const [metricId, setMetricId] = useState('TotalEHP')
  const [perPoint, setPerPoint] = useState(false)
  const [hover, setHover] = useState<NodeCandidate | null>(null)
  const [pinned, setPinned] = useState<NodeCandidate | null>(null)
  const metric = METRICS.find((m) => m.id === metricId) ?? METRICS[0]

  const view = useMemo(() => {
    const raw = nodes.map((node) => {
      const cells = cellsOf(node.card)
      const dpsCell = node.card.core.find((g) => g.group === 'Damage')?.rows[0]?.cells[0]
      const div = perPoint ? Math.max(1, node.cost) : 1
      return {
        node,
        key: node.type === 'Keystone',
        x: pctChange(num(dpsCell?.before), num(dpsCell?.after)) / div,
        y: pctChange(metric.value(cells, 'before'), metric.value(cells, 'after')) / div,
      }
    })
    const frontier = new Set(raw.filter((r) => !raw.some((o) => o.x >= r.x && o.y >= r.y && (o.x > r.x || o.y > r.y))).map((r) => r.node.id))
    const points: Point[] = raw.map((r) => ({ ...r, frontier: frontier.has(r.node.id) }))
    const moved = (v: number) => Math.abs(v) > EPS
    const both = points.filter((p) => moved(p.x) && moved(p.y))
    const xOnly = points.filter((p) => moved(p.x) && !moved(p.y))
    const yOnly = points.filter((p) => !moved(p.x) && moved(p.y))
    const neither = points.length - both.length - xOnly.length - yOnly.length
    const X = robustDomain(points.map((p) => p.x))
    const Y = robustDomain(points.map((p) => p.y))
    const outside = points.filter((p) => p.x < X[0] || p.x > X[1] || p.y < Y[0] || p.y > Y[1])
    // Pick something to show before any hover: the frontier passive with the most damage.
    const best = points.filter((p) => p.frontier).sort((a, b) => b.x - a.x)[0]?.node ?? null
    return { both, xOnly, yOnly, neither, X, Y, outside, best, frontierCount: frontier.size }
  }, [nodes, metric, perPoint])

  const pick = useCallback((n: NodeCandidate) => setPinned((p) => (p?.id === n.id ? null : n)), [])
  const shown = hover ?? pinned ?? view.best
  const unit = perPoint ? '% per point' : '%'

  return (
    <section className="node-scatter" aria-labelledby="node-scatter-title">
      <div className="block-head">
        <h3 id="node-scatter-title">Damage against {lower(metric.label)}</h3>
        <div className="ns-controls">
          <label className="ns-select">
            Defence
            <select value={metricId} onChange={(e) => setMetricId(e.target.value)}>
              {METRICS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
          <div className="segmented" role="group" aria-label="Change measured as">
            <button className={!perPoint ? 'on' : ''} aria-pressed={!perPoint} onClick={() => setPerPoint(false)}>Total</button>
            <button className={perPoint ? 'on' : ''} aria-pressed={perPoint} onClick={() => setPerPoint(true)}>Per point spent</button>
          </div>
        </div>
      </div>
      <p className="muted small-print">
        Each dot is a passive (◆ keystone), sized by points spent. <span className="ns-key" aria-hidden /> Nothing else beats it
        on both ({view.frontierCount}). A passive that changes only one side sits in the strip along that axis.
        Hover to see its card, click to keep it.
      </p>
      <ScatterPlot view={view} unit={unit} metricLabel={metric.label} onHover={setHover} onPick={pick} pinnedId={pinned?.id ?? null} />
      {view.outside.length > 0 && (
        <p className="muted small-print">
          Pinned at the edge:{' '}
          {view.outside.map((p, i) => (
            <span key={p.node.id}>
              {i > 0 && ' · '}
              {p.key ? '◆ ' : ''}{p.node.name}{' '}
              {Math.abs(p.x) > EPS && `${p.x > 0 ? '+' : '−'}${formatNumber(Math.abs(p.x))}${unit} DPS`}
              {Math.abs(p.x) > EPS && Math.abs(p.y) > EPS && ', '}
              {Math.abs(p.y) > EPS && `${p.y > 0 ? '+' : '−'}${formatNumber(Math.abs(p.y))}${unit} ${lower(metric.label)}`}
            </span>
          ))}
        </p>
      )}
      {shown && (
        <div className="ns-card">
          <p className="muted small-print">{pinned && shown.id === pinned.id ? 'Kept (click it again to let go)' : hover ? 'Under the pointer' : 'Most damage among the unbeaten'}</p>
          <NodeCard node={shown} />
        </div>
      )}
    </section>
  )
}

interface View {
  both: Point[]; xOnly: Point[]; yOnly: Point[]; neither: number
  X: [number, number]; Y: [number, number]
}

function ScatterPlot({ view, unit, metricLabel, onHover, onPick, pinnedId }: {
  view: View; unit: string; metricLabel: string; pinnedId: number | null
  onHover: (n: NodeCandidate | null) => void; onPick: (n: NodeCandidate) => void
}) {
  const [ref, width] = useWidth()

  useEffect(() => {
    const root = ref.current
    if (!root || width === 0) return
    const left = root.querySelector<HTMLDivElement>('.ns-left')!
    const main = root.querySelector<HTMLDivElement>('.ns-main')!
    const bottom = root.querySelector<HTMLDivElement>('.ns-bottom')!
    const mainW = width - STRIP
    const clamp = (v: number, [a, b]: [number, number]) => Math.max(a, Math.min(b, v))
    const fmt = (d: number) => `${formatNumber(d)}%`
    const dot = {
      r: (p: Point) => p.node.cost,
      symbol: (p: Point) => (p.key ? 'diamond' : 'circle'),
      fill: (p: Point) => (p.frontier ? C.accent : C.text),
      fillOpacity: (p: Point) => (p.frontier ? 1 : 0.4),
      stroke: (p: Point) => (p.node.id === pinnedId ? C.text : C.panel),
      strokeWidth: (p: Point) => (p.node.id === pinnedId ? 2 : 1),
    }
    // Highlight under the pointer: the same dot outlined, so dodged positions match exactly.
    const ring = { r: (p: Point) => p.node.cost, fill: 'none', stroke: C.accent, strokeWidth: 2.5 }
    const listen = (plot: ReturnType<typeof Plot.plot>) => {
      plot.addEventListener('input', () => onHover((plot as unknown as { value: Point | null }).value?.node ?? null))
      plot.addEventListener('click', () => {
        const v = (plot as unknown as { value: Point | null }).value
        if (v) onPick(v.node)
      })
      plot.style.cursor = 'pointer'
      return plot
    }
    const both = view.both.map((p) => ({ ...p, cx: clamp(p.x, view.X), cy: clamp(p.y, view.Y) }))
    const yOnly = view.yOnly.map((p) => ({ ...p, cy: clamp(p.y, view.Y) }))
    const xOnly = view.xOnly.map((p) => ({ ...p, cx: clamp(p.x, view.X) }))

    const mainPlot = listen(Plot.plot({
      width: mainW, height: MAIN_H, marginLeft: 4, marginRight: 12, marginTop: 18, marginBottom: 6, style: STYLE,
      x: { domain: view.X, axis: null }, y: { domain: view.Y, axis: null }, r: R,
      marks: [
        Plot.gridX({ ticks: 6, stroke: C.border, strokeOpacity: 1 }),
        Plot.gridY({ ticks: 6, stroke: C.border, strokeOpacity: 1 }),
        Plot.ruleX([0], { stroke: C.axis }), Plot.ruleY([0], { stroke: C.axis }),
        Plot.dot(both, { x: 'cx', y: 'cy', ...dot }),
        Plot.text(both.filter((p) => p.frontier), {
          x: 'cx', y: 'cy', text: (p: Point) => p.node.name, dx: 9, textAnchor: 'start', fill: C.text, fontWeight: 600,
        }),
        Plot.dot(both, Plot.pointer({ x: 'cx', y: 'cy', ...ring, maxRadius: 30 })),
        Plot.text(['Both change'], { frameAnchor: 'top-right', fill: C.muted, dy: -14 }),
        // Unbeaten passives in the strip below get their name just above it.
        Plot.text(xOnly.filter((p) => p.frontier), {
          x: 'cx', y: view.Y[0], dy: -2, dx: 4, textAnchor: 'end', lineAnchor: 'bottom',
          text: (p: Point) => `${p.node.name} ↓`, fill: C.text, fontWeight: 600,
        }),
      ],
    }))
    const leftPlot = listen(Plot.plot({
      width: STRIP, height: MAIN_H, marginLeft: 44, marginRight: 2, marginTop: 18, marginBottom: 6, style: STYLE,
      y: { domain: view.Y, ticks: 6, tickFormat: fmt, label: `↑ ${metricLabel}${unit === '%' ? '' : ' per point'}` },
      x: { axis: null }, r: R,
      marks: [
        Plot.dot(yOnly, Plot.dodgeX('right', { y: 'cy', ...dot, padding: 1 })),
        Plot.dot(yOnly, Plot.pointer(Plot.dodgeX('right', { y: 'cy', ...ring, padding: 1, maxRadius: 30 }))),
      ],
    }))
    const bottomPlot = listen(Plot.plot({
      width: mainW, height: BOTTOM_H, marginLeft: 4, marginRight: 12, marginTop: 0, marginBottom: 36, style: STYLE,
      x: { domain: view.X, ticks: 6, tickFormat: fmt, label: `DPS change${unit === '%' ? '' : ' per point'} →` },
      y: { axis: null }, r: R,
      marks: [
        Plot.dot(xOnly, Plot.dodgeY('top', { x: 'cx', ...dot, padding: 1 })),
        Plot.dot(xOnly, Plot.pointer(Plot.dodgeY('top', { x: 'cx', ...ring, padding: 1, maxRadius: 30 }))),

      ],
    }))
    for (const p of [mainPlot, leftPlot, bottomPlot]) p.addEventListener('pointerleave', () => onHover(null))
    main.replaceChildren(mainPlot)
    left.replaceChildren(leftPlot)
    bottom.replaceChildren(bottomPlot)
    return () => { main.replaceChildren(); left.replaceChildren(); bottom.replaceChildren() }
  }, [view, unit, metricLabel, width, ref, onHover, onPick, pinnedId])

  return (
    <div className="ns-grid" ref={ref} role="img" aria-label={`Reachable passives: damage change against ${lower(metricLabel)} change`}>
      <div className="ns-left" />
      <div className="ns-main" />
      <div className="ns-corner muted">{view.neither} change<br />neither</div>
      <div className="ns-bottom" />
    </div>
  )
}
