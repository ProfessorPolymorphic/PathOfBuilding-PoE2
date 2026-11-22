import type { BuildSummary } from '../api'

export function BuildDetails({ build }: { build: BuildSummary }) {
  const groups = build.skills.filter((g) => g.gems.length > 0)
  return (
    <section className="panel details" aria-label="Build details">
      <details open>
        <summary><h3>Skills</h3></summary>
        <ul className="skill-list">
          {groups.map((g) => (
            <li key={g.index} className={g.isMain ? 'skill main' : 'skill'}>
              <div className="skill-head">
                <span className="skill-name">{g.activeSkill ?? g.label ?? `Group ${g.index}`}</span>
                {g.isMain && <span className="badge">main</span>}
                {g.slot && <span className="skill-slot">{g.slot}</span>}
              </div>
              <div className="gems">
                {g.gems.filter((gem) => gem.support).map((gem) => (
                  <span key={gem.name} className={gem.enabled ? 'gem' : 'gem off'}>{gem.name}</span>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </details>

      <details>
        <summary><h3>Gear</h3></summary>
        <ul className="item-list">
          {build.items.map((it) => (
            <li key={it.slot}>
              <span className="item-slot">{it.slot}</span>
              <span className={`item-name rarity-${it.rarity.toLowerCase()}`}>{it.name}</span>
            </li>
          ))}
        </ul>
      </details>

      <details>
        <summary>
          <h3>Passive tree</h3>
          <span className="muted">{build.passivePoints} points · {build.ascendancyPoints} ascendancy</span>
        </summary>
        {build.keystones.length > 0 && (
          <p><span className="muted">Keystones: </span>{build.keystones.join(', ')}</p>
        )}
        <p><span className="muted">Notables: </span>{build.notables.join(', ')}</p>
      </details>
    </section>
  )
}
