import type { StatRow, Stats } from '../api'
import { formatStat } from '../format'

interface Group {
  title: string
  stats: string[]
}

const GROUPS: Group[] = [
  { title: 'Offence', stats: ['TotalDPS', 'CombinedDPS', 'FullDPS', 'AverageDamage', 'Speed', 'CritChance', 'CritMultiplier', 'HitChance'] },
  { title: 'Defence', stats: ['Life', 'EnergyShield', 'Mana', 'TotalEHP', 'Armour', 'Evasion', 'PhysicalDamageReduction', 'EvadeChance'] },
  { title: 'Max hit taken', stats: ['PhysicalMaximumHitTaken', 'FireMaximumHitTaken', 'ColdMaximumHitTaken', 'LightningMaximumHitTaken', 'ChaosMaximumHitTaken'] },
]

const RESISTS = [
  { stat: 'FireResist', label: 'Fire', tone: 'fire' },
  { stat: 'ColdResist', label: 'Cold', tone: 'cold' },
  { stat: 'LightningResist', label: 'Lightning', tone: 'lightning' },
  { stat: 'ChaosResist', label: 'Chaos', tone: 'chaos' },
]

const MAX_HIT_LABELS: Record<string, string> = {
  PhysicalMaximumHitTaken: 'Physical',
  FireMaximumHitTaken: 'Fire',
  ColdMaximumHitTaken: 'Cold',
  LightningMaximumHitTaken: 'Lightning',
  ChaosMaximumHitTaken: 'Chaos',
}

function byStat(rows: StatRow[]): Map<string, StatRow> {
  const map = new Map<string, StatRow>()
  for (const r of rows) if (!map.has(r.stat)) map.set(r.stat, r) // first row wins (sidebar order)
  return map
}

export function StatPanel({ stats }: { stats: Stats }) {
  const rows = byStat(stats.player)

  return (
    <section className="panel stat-panel" aria-label="Character stats">
      {GROUPS.slice(0, 2).map((g) => (
        <StatGroup key={g.title} group={g} rows={rows} />
      ))}

      <div className="stat-group">
        <h3>Resistances</h3>
        <ul className="resists">
          {RESISTS.map(({ stat, label, tone }) => {
            const row = rows.get(stat)
            if (!row || typeof row.value !== 'number') return null
            const over = rows.get(`${stat}OverCap`)
            const overVal = typeof over?.value === 'number' ? over.value : 0
            // Capped if the engine reports overcap; chaos is shown neutrally when uncapped.
            const state = overVal > 0 || row.value >= 75 ? 'capped' : stat === 'ChaosResist' ? 'neutral' : 'low'
            return (
              <li key={stat} className={`resist ${tone} ${state}`}>
                <span className="resist-label">{label}</span>
                <span className="resist-value">{formatStat(stat, row.value)}</span>
                {overVal > 0 && <span className="resist-over">+{overVal}% over</span>}
              </li>
            )
          })}
        </ul>
      </div>

      <StatGroup group={GROUPS[2]} rows={rows} labels={MAX_HIT_LABELS} />

      {stats.minion && stats.minion.length > 0 && (
        <details className="stat-group">
          <summary><h3>Minion</h3></summary>
          <AllStats rows={stats.minion} />
        </details>
      )}

      <details className="stat-group all-stats">
        <summary><h3>All stats</h3></summary>
        <AllStats rows={stats.player} />
      </details>
    </section>
  )
}

function StatGroup({ group, rows, labels }: { group: Group; rows: Map<string, StatRow>; labels?: Record<string, string> }) {
  const present = group.stats.map((s) => rows.get(s)).filter((r): r is StatRow => !!r)
  if (present.length === 0) return null
  return (
    <div className="stat-group">
      <h3>{group.title}</h3>
      <dl className="stat-list">
        {present.map((r) => (
          <div key={r.stat} className="stat-row">
            <dt>{labels?.[r.stat] ?? r.label}</dt>
            <dd>{formatStat(r.stat, r.value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function AllStats({ rows }: { rows: StatRow[] }) {
  return (
    <dl className="stat-list compact">
      {rows.map((r, i) => (
        <div key={`${r.stat}-${i}`} className="stat-row">
          <dt>{r.label}</dt>
          <dd>{formatStat(r.stat, r.value)}</dd>
        </div>
      ))}
    </dl>
  )
}
