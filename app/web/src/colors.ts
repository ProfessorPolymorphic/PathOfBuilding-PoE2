// Chart colours, kept in step with the tokens in index.css (Plot needs literal colours).
// Hue means damage type. Hits: non-crits are the text colour, crits the accent (the one
// strong colour, so crits stand out).
// Validated on the panel surface #121917 with the dataviz palette checker: every pair of
// damage types is at least ΔE 12 apart for colour-blind readers and 24 for full colour vision.

export const DAMAGE_COLORS: Record<string, string> = {
  Physical: '#70675d',
  Fire: '#f9601f',
  Cold: '#37b4dc',
  Lightning: '#f4ce23',
  Chaos: '#b151e3',
}
export const DAMAGE_ORDER = ['Physical', 'Fire', 'Cold', 'Lightning', 'Chaos']

export const OUTCOME_COLORS = { miss: '#98a5a2', hit: '#e7edeb', crit: '#60d3ac' } as const

export const PALE_INK = '#eef8f4'
export const DARK_INK = '#121917'

// Text drawn on top of a damage-type fill.
export function inkOn(type: string): string {
  return type === 'Physical' || type === 'Chaos' ? PALE_INK : DARK_INK
}
