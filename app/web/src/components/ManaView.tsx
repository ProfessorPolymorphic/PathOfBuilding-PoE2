import * as Plot from '@observablehq/plot'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type BuildSummary, type GemCompareResult, type ManaSustain, type SkillCost } from '../api'
import { OUTCOME_COLORS } from '../colors'
import { formatNumber } from '../format'
import { useWidth } from '../useWidth'
import type { ChatEntry } from './Chat'

// Mana tab: which skills drain mana, how fast the pool empties while one is held, and what a
// support swap would change. Every number is the engine's (GET /sustain, POST /compare/gems):
// each skill is calculated as if it were the main skill and used continuously.
// Colour follows the app's convention: the build as it is in the text colour, a what-if in
// the accent.

const C = { now: OUTCOME_COLORS.hit, whatIf: OUTCOME_COLORS.crit, muted: '#98a5a2', border: '#293330', panel: '#121917' }
const STYLE = { background: 'transparent', color: 'var(--muted)', fontSize: '12px', fontFamily: 'inherit' }

interface Swap {
  remove: string[]
  add: string[]
  source: 'chat' | 'picker'
}

interface Props {
  buildId: string
  build: BuildSummary
  entries: ChatEntry[]
}

const signed = (v: number) => `${v >= 0 ? '+' : '−'}${formatNumber(Math.abs(v))}`

// Uses per second the pool can sustain once it's empty: regen pays for each use, less what
// each use leeches back. Capped at the full rate.
function sustainedRate(m: ManaSustain): number | null {
  if (!m.usesPerSecond || m.costPerUse <= 0) return null
  const perUse = m.costPerUse - m.leechAndOnHitPerSecond / m.usesPerSecond
  if (perUse <= 0) return m.usesPerSecond
  return Math.min(m.usesPerSecond, m.regenPerSecond / perUse)
}

function drainSummary(m: ManaSustain): string {
  if (m.secondsToEmpty === undefined) return 'never runs dry'
  const r = sustainedRate(m)
  if (r === null || !m.usesPerSecond) return `empty in ${formatNumber(m.secondsToEmpty)} s`
  return `empty in ${formatNumber(m.secondsToEmpty)} s, then ${formatNumber(r)} uses/s `
    + `(${formatNumber((r / m.usesPerSecond) * 100)}% of full speed)`
}

const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : [])

// The latest support swap the chat agent tested on this skill, if any.
function chatSwapFor(entries: ChatEntry[], skill: SkillCost): Swap | null {
  for (let i = entries.length - 1; i >= 0; i--) {
    const calls = entries[i].toolCalls ?? []
    for (let j = calls.length - 1; j >= 0; j--) {
      const c = calls[j]
      if (c.name !== 'compare_gems' || !c.ok) continue
      const g = c.args.group
      const matches = String(g) === String(skill.group)
        || (typeof g === 'string' && g.toLowerCase() === skill.skill.toLowerCase())
      if (matches) return { remove: list(c.args.remove), add: list(c.args.add), source: 'chat' }
    }
  }
  return null
}

const swapLabel = (s: { remove: string[]; add: string[] }) =>
  [...s.remove.map((r) => `−${r}`), ...s.add.map((a) => `+${a}`)].join(', ')

export function ManaView({ buildId, build, entries }: Props) {
  const [skills, setSkills] = useState<SkillCost[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [group, setGroup] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    api.sustain(buildId)
      .then((d) => { if (!cancelled) { setSkills(d.skills); setError(null) } })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)) })
    return () => { cancelled = true }
  }, [buildId, build])

  // Only skills that cost mana, worst drain first.
  const spenders = useMemo(
    () => (skills ?? []).filter((s) => s.mana.spendPerSecond > 0).sort((a, b) => a.mana.netPerSecond - b.mana.netPerSecond),
    [skills],
  )
  const selected = spenders.find((s) => s.group === group) ?? spenders[0] ?? null

  if (error) return <p className="error">{error}</p>
  if (!skills) return <p className="muted"><span className="spinner inline" aria-hidden /> Calculating every skill’s mana…</p>
  if (!selected) return <section className="panel"><p className="muted">None of this build’s skills cost mana.</p></section>

  const skillList = (
    <section>
      <h3>Skill held down</h3>
      <ul className="skill-picker" role="radiogroup" aria-label="Skill held down">
        {spenders.map((s) => (
          <li key={s.group}>
            <label className={s.group === selected.group ? 'selected' : ''}>
              <input type="radio" name="mana-skill" checked={s.group === selected.group} onChange={() => setGroup(s.group)} />
              <span>{s.skill}</span>
              <span className={`badge mana-net${s.mana.netPerSecond < 0 ? ' drain' : ''}`}>{signed(s.mana.netPerSecond)}/s</span>
            </label>
          </li>
        ))}
      </ul>
    </section>
  )

  return (
    <SkillPanel
      key={`${buildId}-${selected.group}`}
      buildId={buildId} build={build} skill={selected} spenders={spenders} entries={entries} skillList={skillList}
    />
  )
}

interface PanelProps {
  buildId: string
  build: BuildSummary
  skill: SkillCost
  spenders: SkillCost[]
  entries: ChatEntry[]
  skillList: ReactNode
}

// One skill: the swap picker (rail) and the charts (main column) share its what-if.
function SkillPanel({ buildId, build, skill, spenders, entries, skillList }: PanelProps) {
  const chatSwap = useMemo(() => chatSwapFor(entries, skill), [entries, skill])
  const chatKey = chatSwap ? swapLabel(chatSwap) : ''
  const [swap, setSwap] = useState<Swap | null>(chatSwap)
  // A new swap tested in chat replaces whatever is shown (adjusted during render, not in an effect).
  const [seenChat, setSeenChat] = useState(chatKey)
  if (chatKey !== seenChat) {
    setSeenChat(chatKey)
    if (chatSwap) setSwap(chatSwap)
  }

  // Each engine answer is tagged with the swap it was for, so a stale one is never shown.
  const key = swap ? `${swapLabel(swap)}|${build.mainSkill ?? ''}` : ''
  const [outcome, setOutcome] = useState<{ key: string; result?: GemCompareResult; error?: string } | null>(null)
  useEffect(() => {
    if (!swap) return
    let cancelled = false
    api.compareGems(buildId, { group: skill.group, remove: swap.remove, add: swap.add })
      .then((r) => { if (!cancelled) setOutcome({ key, result: r }) })
      .catch((err) => { if (!cancelled) setOutcome({ key, error: err instanceof Error ? err.message : String(err) }) })
    return () => { cancelled = true }
  }, [buildId, skill.group, swap, key, build])
  const current = swap && outcome?.key === key ? outcome : null
  const result = current?.result ?? null
  const error = current?.error ?? null
  const testing = swap !== null && current === null

  const supports = build.skills.find((g) => g.index === skill.group)?.gems.filter((g) => g.support && g.enabled).map((g) => g.name) ?? []
  const after = result?.mana.after

  return (
    <div className="dist-view">
      <aside className="dist-rail panel" aria-label="What to show">
        {skillList}
        <SwapPicker
          buildId={buildId} skill={skill} supports={supports} swap={swap} chatSwap={chatSwap}
          onTest={(s) => setSwap(s)} onClear={() => setSwap(null)}
        />
      </aside>

      <div className="dist-main">
        <header className="dist-head">
          <div>
            <h2>{skill.skill} · mana while it’s held down</h2>
            <p className="muted">
              {formatNumber(skill.mana.costPerUse)} mana per use × {formatNumber(skill.mana.usesPerSecond ?? 0)} uses/s
              {' '}= {formatNumber(skill.mana.spendPerSecond)} mana/s, against {formatNumber(skill.mana.regenPerSecond)} regen
              {' '}and {formatNumber(skill.mana.leechAndOnHitPerSecond)} leech/on-hit per second, from {formatNumber(skill.mana.pool)} unreserved mana.
              {' '}PoB’s numbers with your Configuration; flasks and other skills aren’t counted.
            </p>
          </div>
          {testing && <span className="muted nowrap"><span className="spinner inline" aria-hidden /> Testing…</span>}
        </header>

        <p className="mana-lead">
          Now: <strong className={skill.mana.netPerSecond < 0 ? 'bad' : ''}>{signed(skill.mana.netPerSecond)} mana/s</strong>, {drainSummary(skill.mana)}.
          {after && swap && (
            <> With {swapLabel(swap)}: <strong className="accent">{signed(after.netPerSecond)} mana/s</strong>, {drainSummary(after)}
              {damageNote(result!)}.</>
          )}
        </p>
        {error && <p className="error">{error}</p>}
        {result?.warnings?.map((w) => <p key={w} className="warn small-print">⚠ {w}</p>)}

        <section className="dist-block" aria-labelledby="mana-drain-title">
          <div className="block-head"><h3 id="mana-drain-title">Mana pool while holding {skill.skill}</h3></div>
          <DrainChart now={skill.mana} whatIf={after && swap ? { label: swapLabel(swap), mana: after } : null} />
        </section>

        <section className="dist-block" aria-labelledby="mana-skills-title">
          <div className="block-head"><h3 id="mana-skills-title">Mana spent vs recovered, each skill used continuously</h3></div>
          <SpendChart skills={spenders} selected={skill.group} />
          <SpendTable skills={spenders} />
        </section>
      </div>
    </div>
  )
}

function damageNote(r: GemCompareResult): string {
  const d = r.damage && !Array.isArray(r.damage) ? (r.damage.CombinedDPS ?? r.damage.TotalDPS) : undefined
  if (!d) return '; damage unchanged'
  return `; DPS ${signed(d.delta)}${d.pct !== undefined ? ` (${signed(d.pct)}%)` : ''}`
}

interface PickerProps {
  buildId: string
  skill: SkillCost
  supports: string[]
  swap: Swap | null
  chatSwap: Swap | null
  onTest: (s: Swap) => void
  onClear: () => void
}

function SwapPicker({ buildId, skill, supports, swap, chatSwap, onTest, onClear }: PickerProps) {
  const [remove, setRemove] = useState(swap?.remove[0] ?? '')
  const [add, setAdd] = useState(swap?.add[0] ?? '')
  // Show the swap being displayed (e.g. one the chat just tested) in the fields.
  const [shown, setShown] = useState(swap)
  if (swap !== shown) {
    setShown(swap)
    if (swap) { setRemove(swap.remove[0] ?? ''); setAdd(swap.add[0] ?? '') }
  }
  const [options, setOptions] = useState<string[]>([])
  useEffect(() => {
    let cancelled = false
    api.supports(buildId, skill.group).then((r) => { if (!cancelled) setOptions(r.supports) }).catch(() => undefined)
    return () => { cancelled = true }
  }, [buildId, skill.group])

  const addValid = add === '' || options.length === 0 || options.includes(add)
  const canTest = (remove !== '' || add !== '') && addValid
  const listId = `mana-supports-${skill.group}`
  return (
    <section>
      <h3>Test a support swap</h3>
      <div className="swap-fields">
        <label>Remove
          <select value={remove} onChange={(e) => setRemove(e.target.value)}>
            <option value="">(nothing)</option>
            {supports.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label>Add
          <input list={listId} value={add} placeholder="e.g. Efficiency II" onChange={(e) => setAdd(e.target.value)} />
          <datalist id={listId}>{options.map((o) => <option key={o} value={o} />)}</datalist>
        </label>
      </div>
      {!addValid && <p className="error small-print">No support by that name can go on {skill.skill}.</p>}
      <div className="swap-actions">
        <button className="small" disabled={!canTest}
          onClick={() => onTest({ remove: remove ? [remove] : [], add: add ? [add] : [], source: 'picker' })}>
          Test
        </button>
        {swap && <button className="ghost small" onClick={onClear}>Clear</button>}
      </div>
      <p className="muted small-print">
        {swap?.source === 'chat'
          ? 'Showing the swap the chat last tested on this skill.'
          : chatSwap
            ? <>The chat tested {swapLabel(chatSwap)}. <button className="link" onClick={() => onTest(chatSwap)}>Show it</button></>
            : 'Swaps the chat tests on this skill appear here too.'}
        {' '}The build isn’t changed.
      </p>
    </section>
  )
}

// Mana over time while the skill is held. After the pool empties the line stays at zero:
// from then on each use waits for regen.
function DrainChart({ now, whatIf }: { now: ManaSustain; whatIf: { label: string; mana: ManaSustain } | null }) {
  const [ref, width] = useWidth()
  const box = useRef<HTMLDivElement>(null)
  const scen = useMemo(() => [
    { which: 'Now', m: now, color: C.now },
    ...(whatIf ? [{ which: whatIf.label, m: whatIf.mana, color: C.whatIf }] : []),
  ], [now, whatIf])

  useEffect(() => {
    const el = box.current
    if (!el || width === 0) return
    const empties = scen.map((s) => s.m.secondsToEmpty).filter((t): t is number => t !== undefined)
    const horizon = empties.length ? Math.min(120, Math.max(20, Math.ceil(Math.max(...empties) * 1.3 / 5) * 5)) : 30
    const pool = now.pool
    const rows = scen.flatMap((s) => {
      const out = []
      for (let i = 0; i <= 240; i++) {
        const t = (horizon * i) / 240
        out.push({ which: s.which, t, mana: s.m.netPerSecond >= 0 ? s.m.pool : Math.max(0, s.m.pool + s.m.netPerSecond * t) })
      }
      return out
    })
    const ends = scen.filter((s) => s.m.secondsToEmpty !== undefined).map((s, i) => ({ ...s, t: s.m.secondsToEmpty!, i }))
    const plot = Plot.plot({
      width, height: 260, marginLeft: 52, marginRight: 18, marginTop: 24, style: STYLE,
      color: { domain: scen.map((s) => s.which), range: scen.map((s) => s.color) },
      x: { label: 'Seconds holding the skill →', domain: [0, horizon] },
      y: { label: '↑ Mana', domain: [0, pool * 1.05], grid: true, tickFormat: (d: number) => formatNumber(d) },
      marks: [
        Plot.ruleY([0], { stroke: C.border }),
        Plot.lineY(rows, { x: 't', y: 'mana', z: 'which', stroke: 'which', strokeWidth: 2 }),
        Plot.dot(ends, { x: 't', y: 0, fill: 'which', r: 4.5, stroke: C.panel, strokeWidth: 2 }),
        // One text mark per line, stacked so two nearby labels don't overlap.
        ...ends.map((e) => Plot.text([e], {
          x: 't', y: 0, dy: -12 - e.i * 15, text: (d: { t: number }) => `empty at ${formatNumber(d.t)} s`,
          fill: 'currentColor', fontSize: 12, stroke: C.panel, strokeWidth: 4, paintOrder: 'stroke',
        })),
        Plot.ruleX(rows, Plot.pointerX({ x: 't', stroke: C.muted, strokeOpacity: 0.5 })),
        Plot.tip(rows, Plot.pointerX({
          x: 't', y: 'mana', z: 'which', fill: C.panel, stroke: C.border,
          title: (d: { which: string; t: number; mana: number }) =>
            `${d.which}\nafter ${formatNumber(d.t)} s: ${formatNumber(Math.round(d.mana))} mana`,
        })),
      ],
    })
    el.replaceChildren(plot)
    return () => plot.remove()
  }, [scen, width, now.pool])

  return (
    <div className="dist-chart" ref={ref}>
      {scen.length > 1 && (
        <ul className="mana-legend" aria-label="Lines">
          {scen.map((s) => (
            <li key={s.which}><span className="line-key" style={{ background: s.color }} aria-hidden />{s.which}</li>
          ))}
        </ul>
      )}
      <div ref={box} role="img"
        aria-label={scen.map((s) => `${s.which}: ${signed(s.m.netPerSecond)} mana per second, ${drainSummary(s.m)}`).join('. ')} />
    </div>
  )
}

interface SpendRow { skill: string; group: number; spend: number; recover: number; net: number; cost: number; rate: number }

// Bars: mana spent per second. Tick: mana recovered per second while using that skill (regen
// plus its own leech/on-hit). A bar past its tick drains the pool.
function SpendChart({ skills, selected }: { skills: SkillCost[]; selected: number }) {
  const [ref, width] = useWidth()
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = box.current
    if (!el || width === 0) return
    const rows: SpendRow[] = skills.map((s) => ({
      skill: s.skill, group: s.group, spend: s.mana.spendPerSecond,
      recover: s.mana.regenPerSecond + s.mana.leechAndOnHitPerSecond, net: s.mana.netPerSecond,
      cost: s.mana.costPerUse, rate: s.mana.usesPerSecond ?? 0,
    }))
    const max = Math.max(...rows.map((r) => Math.max(r.spend, r.recover)))
    const plot = Plot.plot({
      width, height: 44 + rows.length * 36, marginLeft: 120, marginRight: 64, style: STYLE,
      x: { label: 'Mana per second →', domain: [0, max * 1.05], grid: true },
      y: { label: null, domain: rows.map((r) => r.skill) },
      marks: [
        Plot.barX(rows, {
          y: 'skill', x: 'spend', rx: 3, insetTop: 8, insetBottom: 8,
          fill: C.now, fillOpacity: (d: SpendRow) => (d.group === selected ? 0.6 : 0.35),
        }),
        Plot.tickX(rows, { y: 'skill', x: 'recover', stroke: C.now, strokeWidth: 3, insetTop: 3, insetBottom: 3 }),
        Plot.text(rows, {
          y: 'skill', x: (d: SpendRow) => Math.max(d.spend, d.recover), dx: 8, textAnchor: 'start',
          text: (d: SpendRow) => `${signed(d.net)}/s`, fill: 'currentColor', fontWeight: 600,
        }),
        Plot.tip(rows, Plot.pointerY({
          y: 'skill', x: 'spend', fill: C.panel, stroke: C.border,
          title: (d: SpendRow) => `${d.skill}\n${formatNumber(d.cost)} per use × ${formatNumber(d.rate)}/s = ${formatNumber(d.spend)} spent/s`
            + `\nrecovered ${formatNumber(d.recover)}/s → net ${signed(d.net)}/s`,
        })),
      ],
    })
    el.replaceChildren(plot)
    return () => plot.remove()
  }, [skills, selected, width])

  return (
    <div className="dist-chart" ref={ref}>
      <ul className="mana-legend" aria-label="Key">
        <li><span className="bar-key" aria-hidden />Spent per second</li>
        <li><span className="tick-key" aria-hidden />Recovered per second (regen + that skill’s leech/on-hit)</li>
      </ul>
      <div ref={box} role="img" aria-label="Mana spent and recovered per second for each skill; the table below has the numbers." />
    </div>
  )
}

function SpendTable({ skills }: { skills: SkillCost[] }) {
  return (
    <details className="cc-more mana-table">
      <summary>Table</summary>
      <table className="weak-res">
        <thead>
          <tr><th>Skill</th><th>Per use</th><th>Uses/s</th><th>Spent/s</th><th>Recovered/s</th><th>Net/s</th><th>Empty in</th></tr>
        </thead>
        <tbody>
          {skills.map((s) => (
            <tr key={s.group}>
              <th scope="row">{s.skill}</th>
              <td>{formatNumber(s.mana.costPerUse)}</td>
              <td>{formatNumber(s.mana.usesPerSecond ?? 0)}</td>
              <td>{formatNumber(s.mana.spendPerSecond)}</td>
              <td>{formatNumber(s.mana.regenPerSecond + s.mana.leechAndOnHitPerSecond)}</td>
              <td>{signed(s.mana.netPerSecond)}</td>
              <td>{s.mana.secondsToEmpty !== undefined ? `${formatNumber(s.mana.secondsToEmpty)} s` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}
