import { useMemo } from 'react'
import type { BuildSummary, SkillGroup } from './api'

// The build's active skills, once each, enabled ones only.
export function useSkills(build: BuildSummary): SkillGroup[] {
  return useMemo(() => {
    const seen = new Set<string>()
    return build.skills.filter((g) => {
      if (!g.enabled || !g.activeSkill || seen.has(g.activeSkill)) return false
      seen.add(g.activeSkill)
      return true
    })
  }, [build])
}

export function mainSkill(skills: SkillGroup[]): string {
  return (skills.find((g) => g.isMain) ?? skills[0])?.activeSkill ?? ''
}
