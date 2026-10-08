import type { Position } from './columns'

/**
 * Rotate points about their centre so their long axis (principal axis) runs
 * horizontally, or vertically when `wide` is false: a force layout settles at an
 * arbitrary angle, and turning it to match its container's shape uses the space
 * better without changing the layout itself.
 */
export function orient(points: Position[], wide: boolean): Position[] {
  const n = points.length
  if (n < 2) return points
  const cx = points.reduce((a, p) => a + p.x, 0) / n
  const cy = points.reduce((a, p) => a + p.y, 0) / n
  let sxx = 0
  let syy = 0
  let sxy = 0
  for (const p of points) {
    const [dx, dy] = [p.x - cx, p.y - cy]
    sxx += dx * dx
    syy += dy * dy
    sxy += dx * dy
  }
  const axis = 0.5 * Math.atan2(2 * sxy, sxx - syy) // angle of the long axis
  const turn = (wide ? 0 : Math.PI / 2) - axis
  const [cos, sin] = [Math.cos(turn), Math.sin(turn)]
  return points.map((p) => {
    const [dx, dy] = [p.x - cx, p.y - cy]
    return { x: cx + dx * cos - dy * sin, y: cy + dx * sin + dy * cos }
  })
}
