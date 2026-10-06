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
