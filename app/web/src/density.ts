// Smooth density of sampled damage, one curve per outcome, scaled so each curve's area is
// that outcome's share of attacks. Gaussian kernel (Silverman's bandwidth) applied to a fine
// histogram, which is exact enough at this grid size and fast for 20k samples × 6 scenarios.

const GRID = 400

export interface DensityPoint { x: number; y: number }

function bandwidth(values: number[]): number {
  const n = values.length
  const sorted = [...values].sort((a, b) => a - b)
  const mean = values.reduce((s, v) => s + v, 0) / n
  const sd = Math.sqrt(values.reduce((s, v) => s + (v - mean) ** 2, 0) / Math.max(1, n - 1))
  const iqr = sorted[Math.floor(0.75 * (n - 1))] - sorted[Math.floor(0.25 * (n - 1))]
  const spread = Math.min(sd, iqr / 1.34) || sd || 1
  return 0.9 * spread * n ** -0.2
}

// Density of `values` on [0, max], as share of `total` attacks per `per` damage.
export function density(values: number[], total: number, max: number, per = 100): DensityPoint[] {
  if (values.length < 2 || max <= 0) return []
  const step = max / GRID
  const counts = new Float64Array(GRID + 1)
  for (const v of values) counts[Math.min(GRID, Math.max(0, Math.round(v / step)))]++
  const h = Math.max(bandwidth(values), step)
  const reach = Math.ceil((4 * h) / step)
  const kernel = Array.from({ length: 2 * reach + 1 }, (_, i) => {
    const d = ((i - reach) * step) / h
    return Math.exp(-0.5 * d * d)
  })
  const norm = 1 / (h * Math.sqrt(2 * Math.PI) * total)
  const out: DensityPoint[] = []
  for (let i = 0; i <= GRID; i++) {
    let sum = 0
    for (let k = -reach; k <= reach; k++) {
      const j = i + k
      if (j >= 0 && j <= GRID && counts[j]) sum += counts[j] * kernel[k + reach]
    }
    out.push({ x: i * step, y: sum * norm * per })
  }
  return out
}
