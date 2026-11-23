import type { SkillGroup } from '../api'

export function SkillPicker({ skills, value, onChange, name }: {
  skills: SkillGroup[]; value: string; onChange: (skill: string) => void; name: string
}) {
  return (
    <ul className="skill-picker" role="radiogroup" aria-label="Skill">
      {skills.map((g) => (
        <li key={g.index}>
          <label className={g.activeSkill === value ? 'selected' : ''}>
            <input type="radio" name={name} checked={g.activeSkill === value} onChange={() => onChange(g.activeSkill!)} />
            <span>{g.activeSkill}</span>
            {g.isMain && <span className="badge">main</span>}
          </label>
        </li>
      ))}
    </ul>
  )
}
