import { describe, expect, it } from 'vitest'

import { collapseVersions } from './collapse'
import { COLUMN_GAP, columnPositions } from './columns'
import asthmaCopd from './fixtures/asthma-copd-graph.json'
import chd from './fixtures/chd-graph.json'
import type { KgGraph } from './types'

const CHD = collapseVersions(chd as KgGraph)
const TWO = collapseVersions(asthmaCopd as KgGraph)

describe('columnPositions', () => {
  it('puts concepts, variables and studies in three columns', () => {
    const pos = columnPositions(CHD)
    expect(pos.size).toBe(CHD.nodes.length)
    for (const n of CHD.nodes) {
      expect(pos.get(n.id)!.x).toBe({ concept: 0, variable: COLUMN_GAP, study: 2 * COLUMN_GAP }[n.type])
    }
  })

  it("places each study level with the middle of its variables, which sit together", () => {
    const pos = columnPositions(CHD)
    const fhs = CHD.edges.filter((e) => e.target === 'phs000007').map((e) => pos.get(e.source)!.y)
    expect(Math.max(...fhs) - Math.min(...fhs)).toBe(18 * (fhs.length - 1)) // adjacent rows
    expect(pos.get('phs000007')!.y).toBe(fhs.reduce((a, b) => a + b) / fhs.length)
  })

  it('puts studies shared by two concepts between the ones each has alone', () => {
    const pos = columnPositions(TWO)
    const y = (id: string) => pos.get(id)!.y
    const type = new Map(TWO.nodes.map((n) => [n.id, n.type]))
    const studyOf = new Map(TWO.edges.filter((e) => type.get(e.target) === 'study').map((e) => [e.source, e.target]))
    const conceptsOf = new Map<string, Set<string>>()
    for (const e of TWO.edges) {
      if (type.get(e.target) !== 'concept') continue
      const s = studyOf.get(e.source)!
      conceptsOf.set(s, (conceptsOf.get(s) ?? new Set()).add(e.target))
    }
    const only = (c: string) => [...conceptsOf].filter(([, cs]) => cs.size === 1 && cs.has(c)).map(([s]) => y(s))
    const asthma = only('MONDO:0004979')
    const copd = only('MONDO:0005002')
    for (const shared of ['phs000007', 'phs000280']) {
      expect(y(shared)).toBeGreaterThan(Math.max(...asthma))
      expect(y(shared)).toBeLessThan(Math.min(...copd))
    }
    expect(y('MONDO:0004979')).toBeLessThan(y('MONDO:0005002'))
  })
})
