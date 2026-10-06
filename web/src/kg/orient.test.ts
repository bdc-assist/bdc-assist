import { describe, expect, it } from 'vitest'

import type { Position } from './columns'
import { orient } from './orient'

const extent = (ps: Position[]) => ({
  w: Math.max(...ps.map((p) => p.x)) - Math.min(...ps.map((p) => p.x)),
  h: Math.max(...ps.map((p) => p.y)) - Math.min(...ps.map((p) => p.y)),
})

// a tall, slightly tilted cloud, like a force layout that came out vertical
const TALL: Position[] = Array.from({ length: 40 }, (_, i) => ({ x: 10 + (i % 5) * 8 + i * 0.5, y: i * 20 }))

describe('orient', () => {
  it('turns a tall layout wide for a wide container', () => {
    const before = extent(TALL)
    const after = extent(orient(TALL, true))
    expect(before.h).toBeGreaterThan(before.w)
    expect(after.w).toBeGreaterThan(after.h * 3)
  })

  it('keeps it tall for a tall container', () => {
    const after = extent(orient(TALL, false))
    expect(after.h).toBeGreaterThan(after.w * 3)
  })

  it('only rotates: distances between points stay the same', () => {
    const out = orient(TALL, true)
    const d = (ps: Position[], i: number, j: number) => Math.hypot(ps[i].x - ps[j].x, ps[i].y - ps[j].y)
    for (const [i, j] of [[0, 39], [3, 17], [20, 21]]) expect(d(out, i, j)).toBeCloseTo(d(TALL, i, j))
  })

  it('leaves fewer than two points alone', () => {
    expect(orient([{ x: 1, y: 2 }], true)).toEqual([{ x: 1, y: 2 }])
  })
})
