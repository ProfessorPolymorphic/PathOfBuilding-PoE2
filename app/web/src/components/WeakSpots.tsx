import * as Plot from '@observablehq/plot'
import { useEffect, useState } from 'react'
import { api, type BuildSummary, type WeakSpots as WeakSpotsData, type WeakSpotType } from '../api'
import { DAMAGE_COLORS, DARK_INK, PALE_INK, inkOn } from '../colors'
import { formatNumber } from '../format'
import { useWidth } from '../useWidth'

// Weak spots: max hit by damage type, read against the build itself (no outside thresholds).
// Solid bar: max hit now. Dashed outline: max hit if that resistance were capped (computed by
// the engine). Tick: the life + ES that type of hit lands on, so bar beyond tick = mitigation.

const C = { text: '#e7edeb', muted: '#98a5a2', bad: '#f1786d', panel: '#121917' }
const STYLE = { background: 'transparent', color: 'var(--muted)', fontSize: '12px', fontFamily: 'inherit' }

export function WeakSpots({ buildId, build }: { buildId: string; build: BuildSummary }) {
  const [data, setData] = useState<WeakSpotsData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.weakSpots(buildId)
      .then((d) => { if (!cancelled) { setData(d); setError(null) } })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)) })
    return () => { cancelled = true }
  }, [buildId, build])

  if (error) return <section className="panel weak-spots"><p className="error">{error}</p></section>
  if (!data) return null
  const best = data.types.reduce((a, b) => (b.maxHit > a.maxHit ? b : a))
  const weakest = data.types.find((t) => t.weakest)
  const uncapped = data.types.filter((t) => t.uncapped)
  const biggest = uncapped.reduce<WeakSpotType | null>((a, b) => (!a || (b.gainIfCapped ?? 0) > (a.gainIfCapped ?? 0) ? b : a), null)

  return (
    <section className="panel weak-spots" aria-labelledby="weak-spots-title">
      <h3 id="weak-spots-title">Weak spots</h3>
      {weakest && (
        <p className="weak-lead">
          Weakest: <strong className="bad">▼ {weakest.type} {formatNumber(weakest.maxHit)}</strong>,{' '}
          {formatNumber(weakest.shareOfBest * 100)}% of your best ({best.type} {formatNumber(best.maxHit)}).
          {biggest && <> Capping {biggest.type.toLowerCase()} adds the most: +{formatNumber(biggest.gainIfCapped!)}.</>}
        </p>
      )}
      <WeakChart data={data} />
      <table className="weak-res">
        <thead><tr><th>Resistance</th><th>Now</th><th>Cap</th><th /></tr></thead>
        <tbody>
          {data.types.filter((t) => t.resist !== undefined).map((t) => (
            <tr key={t.type}>
              <th scope="row"><span className="type-key" style={{ background: DAMAGE_COLORS[t.type] }} aria-hidden />{t.type}</th>
              <td className={t.uncapped ? 'bad' : ''}>{formatNumber(t.resist!)}%</td>
              <td>{formatNumber(t.resistMax!)}%</td>
              <td className="muted">
                {t.uncapped ? <span className="bad">{formatNumber(t.missing!)} missing</span>
                  : t.overCap ? `${formatNumber(t.overCap)} over` : 'capped'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small-print">
        Max hit: the biggest single hit of that type you survive. Tick: the life and energy shield it lands on.{' '}
        <a href={api.weakSpotsDownloadUrl(buildId)} download>Download the data</a>
      </p>
    </section>
  )
}

function WeakChart({ data }: { data: WeakSpotsData }) {
  const [ref, width] = useWidth()

  useEffect(() => {
    const el = ref.current
    if (!el || width === 0) return
    const rows = data.types
    const xmax = Math.max(...rows.map((t) => Math.max(t.maxHit, t.cappedMaxHit ?? 0, t.pool))) * 1.04
    const capped = rows.filter((t) => t.cappedMaxHit)
    const color = (t: WeakSpotType) => DAMAGE_COLORS[t.type]
    const long = (t: WeakSpotType) => t.maxHit / xmax > 0.78
    const chart = Plot.plot({
      width,
      height: 34 * rows.length + 40,
      marginLeft: 70,
      marginRight: 18,
      marginBottom: 34,
      style: STYLE,
      x: { domain: [0, xmax], label: 'Max hit taken →', grid: true, ticks: Math.max(2, Math.floor(width / 90)), tickFormat: (d: number) => formatNumber(d) },
      y: { domain: rows.map((t) => t.type), label: null, padding: 0.28 },
      marks: [
        Plot.barX(capped, {
          x1: 'maxHit', x2: 'cappedMaxHit', y: 'type', fill: 'none', stroke: color,
          strokeDasharray: '4 3', strokeOpacity: 0.85,
        }),
        Plot.barX(rows, {
          x: 'maxHit', y: 'type', fill: color, rx: 2,
          channels: { 'Max hit': 'maxHit', 'If capped': 'cappedMaxHit', Pool: 'pool' },
          tip: { format: { x: false, y: true, fill: false, 'Max hit': (d: number) => formatNumber(d), 'If capped': (d: number) => (d ? formatNumber(d) : 'capped'), Pool: (d: number) => formatNumber(d) } },
        }),
        Plot.tickX(rows, { x: 'pool', y: 'type', stroke: C.text, strokeWidth: 2 }),
        // Value after the bar (for an uncapped type, inside the dashed outline); a bar that
        // nearly fills the chart carries its value inside, in ink that reads on its colour.
        Plot.text(rows.filter((t) => !long(t)), {
          x: 'maxHit', y: 'type', dx: 5, textAnchor: 'start', fontWeight: 600, fill: C.text,
          text: (t: WeakSpotType) => `${t.weakest ? '▼ ' : ''}${formatNumber(t.maxHit)}`,
        }),
        ...[DARK_INK, PALE_INK].map((ink) => Plot.text(rows.filter((t) => long(t) && inkOn(t.type) === ink), {
          x: 'maxHit', y: 'type', dx: -5, textAnchor: 'end', fontWeight: 600, fill: ink,
          text: (t: WeakSpotType) => `${t.weakest ? '▼ ' : ''}${formatNumber(t.maxHit)}`,
        })),
        // Gain label only where the outline is wide enough to hold it next to the value.
        Plot.text(capped.filter((t) => ((t.gainIfCapped ?? 0) / xmax) * (width - 78) > 110), {
          x: 'cappedMaxHit', y: 'type', dx: -5, textAnchor: 'end', fill: C.muted,
          text: (t: WeakSpotType) => `+${formatNumber(t.gainIfCapped ?? 0)}`,
        }),
      ],
    })
    el.replaceChildren(chart)
    return () => { el.replaceChildren() }
  }, [data, width, ref])

  return <div className="weak-chart" ref={ref} role="img" aria-label="Max hit taken by damage type, and if each resistance were capped" />
}
