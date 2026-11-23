import type { ReactNode } from 'react'
import type { Card, CardCell, CardRow, OtherChange } from '../api'
import { formatNumber } from '../format'

// Change preview card (app/VISION.md): every candidate change is shown against the same core
// stats, so cards can be compared at a glance. Gains and losses carry an arrow as well as a
// colour. Every number is the engine's, as the API returned it.

interface Props {
  card: Card
  title: ReactNode
  subtitle?: ReactNode
  // 'baseline' shows the build as it is: values only, no deltas.
  mode?: 'change' | 'baseline'
  children?: ReactNode
}

export function ChangeCard({ card, title, subtitle, mode = 'change', children }: Props) {
  const changed = card.core.some((g) => g.rows.some((r) => r.cells.some((c) => c.delta !== undefined)))
  return (
    <article className={`change-card ${mode}`}>
      <header className="cc-head">
        <div className="cc-title">{title}</div>
        {subtitle && <div className="cc-subtitle">{subtitle}</div>}
      </header>
      {mode === 'change' && !changed && card.other.length === 0 && (
        <p className="cc-none">No change to any stat.</p>
      )}
      {card.core.map((g) => (
        <section key={g.group} className="cc-group" aria-label={g.group}>
          <h4>{g.group}</h4>
          {g.rows.map((row, i) =>
            row.label ? <MultiRow key={i} row={row} mode={mode} /> : <SingleRow key={i} cell={row.cells[0]} mode={mode} />,
          )}
        </section>
      ))}
      {card.other.length > 0 && (
        <section className="cc-group cc-other" aria-label="Other changes">
          <h4>Also changes</h4>
          {card.other.map((o) => <OtherRow key={o.stat} change={o} />)}
        </section>
      )}
      {children}
    </article>
  )
}

function value(cell: CardCell, v: number | string | undefined): string {
  if (v === undefined) return '—'
  if (typeof v !== 'number') return v
  return formatNumber(v) + (cell.unit ?? '')
}

function direction(better?: boolean, delta?: number) {
  if (delta === undefined) return 'same'
  return better ? 'up' : 'down'
}

function Delta({ cell, showPct = true }: { cell: CardCell; showPct?: boolean }) {
  if (cell.delta === undefined) return null
  const arrow = cell.better ? '▲' : '▼'
  const sign = cell.delta > 0 ? '+' : '−'
  const amount = formatNumber(Math.abs(cell.delta)) + (cell.unit ?? '')
  const pct = showPct && cell.pct !== undefined && !cell.unit ? ` (${sign}${formatNumber(Math.abs(cell.pct))}%)` : ''
  return (
    <span className="cc-delta">
      <span aria-hidden>{arrow} </span>
      {sign}{amount}{pct}
    </span>
  )
}

function SingleRow({ cell, mode }: { cell: CardCell; mode: Props['mode'] }) {
  const dir = direction(cell.better, cell.delta)
  return (
    <div className={`cc-row ${dir}`}>
      <span className="cc-label">{cell.label}</span>
      <span className="cc-value">{value(cell, mode === 'baseline' ? cell.before : cell.after)}</span>
      {mode === 'change' && <span className="cc-change"><Delta cell={cell} /></span>}
    </div>
  )
}

// Several stats on one line: resistances, max hit by damage type.
function MultiRow({ row, mode }: { row: CardRow; mode: Props['mode'] }) {
  return (
    <div className="cc-multi">
      <span className="cc-label">{row.label}</span>
      <ul data-cols={row.cells.length} style={{ '--cols': row.cells.length } as React.CSSProperties}>
        {row.cells.map((cell) => (
          <li key={cell.key} className={`cc-cell ${direction(cell.better, cell.delta)} tone-${cell.tone ?? 'none'}`}>
            <span className="cc-cell-label">{cell.label}</span>
            <span className="cc-value">{value(cell, mode === 'baseline' ? cell.before : cell.after)}</span>
            {mode === 'change' && <Delta cell={cell} showPct={false} />}
          </li>
        ))}
      </ul>
    </div>
  )
}

function OtherRow({ change }: { change: OtherChange }) {
  const cell: CardCell = {
    key: change.stat, label: change.label, before: change.before, after: change.after,
    delta: typeof change.delta === 'number' ? change.delta : undefined,
    pct: typeof change.pct === 'number' ? change.pct : undefined,
    better: change.better,
  }
  return <SingleRow cell={cell} mode="change" />
}
