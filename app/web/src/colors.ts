// Chart colours, kept in step with the tokens in index.css (Plot needs literal colours).
// Hue only ever means damage type; outcome is brightness (miss dim, crit bright).
// Validated on the panel surface #17150f with the dataviz palette checker: every pair of
// damage types is at least ΔE 12 apart for colour-blind readers and 24 for full colour vision.

export const DAMAGE_COLORS: Record<string, string> = {
  Physical: '#70675d',
  Fire: '#f9601f',
  Cold: '#37b4dc',
  Lightning: '#f4ce23',
  Chaos: '#b151e3',
}
export const DAMAGE_ORDER = ['Physical', 'Fire', 'Cold', 'Lightning', 'Chaos']

export const OUTCOME_COLORS = { miss: '#4a463d', hit: '#9d9482', crit: '#f3ead6' } as const

// Text drawn on top of a damage-type fill.
export function inkOn(type: string): string {
  return type === 'Physical' || type === 'Chaos' ? '#f3ead6' : '#17150f'
}
