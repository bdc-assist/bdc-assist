import { describe, expect, it } from 'vitest'

import { collapseVersions } from './collapse'
import asthmaCopd from './fixtures/asthma-copd-graph.json'
import chd from './fixtures/chd-graph.json'
import { studyList } from './list'
import type { KgGraph } from './types'

const CHD = collapseVersions(chd as KgGraph)
const TWO = collapseVersions(asthmaCopd as KgGraph)

describe('studyList', () => {
  it('groups every variable under its study', () => {
    const list = studyList(CHD)
    expect(list).toHaveLength(7)
    expect(list.flatMap((s) => s.variables)).toHaveLength(15)
    const fhs = list.find((s) => s.study?.id === 'phs000007')!
    expect(fhs.variables.map((v) => v.variable.label)).toEqual(['FB230', 'FC219'])
    expect(fhs.concepts.map((c) => c.id)).toEqual(['MONDO:0005453'])
  })

  it('sorts studies by name when they cover the same concepts', () => {
    const names = studyList(CHD).map((s) => s.study!.label)
    expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b)))
  })

  it('puts studies shared by several concepts first', () => {
    const list = studyList(TWO)
    expect(list.slice(0, 2).map((s) => s.study!.id).sort()).toEqual(['phs000007', 'phs000280'])
    expect(list[0].concepts.map((c) => c.id)).toEqual(['MONDO:0004979', 'MONDO:0005002']) // graph order
    expect(list.slice(2).every((s) => s.concepts.length === 1)).toBe(true)
  })

  it('groups each study\'s variables by concept', () => {
    const fhs = studyList(TWO).find((s) => s.study?.id === 'phs000007')!
    expect(fhs.byConcept.map((g) => g.concept?.id)).toEqual(['MONDO:0004979', 'MONDO:0005002'])
    expect(fhs.byConcept.reduce((a, g) => a + g.variables.length, 0)).toBe(fhs.variables.length) // none on both here
    for (const g of fhs.byConcept) expect(g.variables.every((v) => v.concepts.includes(g.concept!))).toBe(true)
    // one concept: one group per study, holding all its variables
    for (const s of studyList(CHD)) expect(s.byConcept.map((g) => g.variables.length)).toEqual([s.variables.length])
  })

  it('puts a variable on two concepts in both groups', () => {
    const [s] = studyList({
      nodes: [
        { id: 'A', label: 'a', type: 'concept' },
        { id: 'B', label: 'b', type: 'concept' },
        { id: 'v', label: 'copd or asthma', type: 'variable' },
        { id: 'S', label: 's', type: 'study' },
      ],
      edges: [
        { source: 'v', target: 'A' },
        { source: 'v', target: 'B' },
        { source: 'v', target: 'S' },
      ],
    })
    expect(s.variables).toHaveLength(1)
    expect(s.byConcept.map((g) => [g.concept?.id, g.variables.length])).toEqual([['A', 1], ['B', 1]])
  })

  it("carries each variable's concepts and weight", () => {
    const hd = studyList(CHD).flatMap((s) => s.variables).find((v) => v.variable.label === 'HEARTDIS')!
    expect(hd.concepts.map((c) => c.id)).toEqual(['MONDO:0005453'])
    expect(hd.weight).toBe(0) // 9 related concepts, the fewest
  })

  it('keeps variables with no study, last', () => {
    const list = studyList({
      nodes: [
        { id: 'C', label: 'c', type: 'concept' },
        { id: 'v0', label: 'loose', type: 'variable' },
        { id: 'v1', label: 'kept', type: 'variable' },
        { id: 'S', label: 's', type: 'study' },
      ],
      edges: [
        { source: 'v0', target: 'C' },
        { source: 'v1', target: 'C' },
        { source: 'v1', target: 'S' },
      ],
    })
    expect(list.map((s) => s.study?.id ?? null)).toEqual(['S', null])
  })
})
