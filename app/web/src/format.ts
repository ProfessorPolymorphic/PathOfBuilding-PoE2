// Number formatting for engine stats.

const PERCENT = new Set([
  'CritChance', 'EffectiveCritChance', 'HitChance', 'PhysicalDamageReduction', 'EvadeChance',
  'DeflectChance', 'EffectiveBlockChance', 'EffectiveSpellBlockChance',
  'FireResist', 'ColdResist', 'LightningResist', 'ChaosResist',
  'FireResistOverCap', 'ColdResistOverCap', 'LightningResistOverCap', 'ChaosResistOverCap',
])

export function formatStat(stat: string, value: number | string): string {
  if (typeof value !== 'number') return String(value)
  if (PERCENT.has(stat)) return `${round(value)}%`
  if (stat === 'Speed') return `${value.toFixed(2)}/s`
  if (stat === 'CritMultiplier') return `${value.toFixed(2)}×`
  return round(value)
}

function round(v: number): string {
  const abs = Math.abs(v)
  const digits = abs >= 100 || Number.isInteger(v) ? 0 : abs >= 10 ? 1 : 2
  return v.toLocaleString(undefined, { maximumFractionDigits: digits })
}
