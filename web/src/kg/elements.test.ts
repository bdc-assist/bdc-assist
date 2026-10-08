import { describe, expect, it } from 'vitest'

import { collapseVersions } from './collapse'
import { toElements } from './elements'
import chd from './fixtures/chd-graph.json'
import type { KgGraph } from './types'

const CHD = collapseVersions(chd as KgGraph)

describe('toElements', () => {
  it('makes one element per node and edge', () => {
    const els = toElements(CHD)
    expect(els.filter((e) => e.group === 'nodes')).toHaveLength(CHD.nodes.length)
    expect(els.filter((e) => e.group === 'edges')).toHaveLength(CHD.edges.length)
    expect(els.find((e) => e.data.id === 'phs000007')?.data).toEqual({
      id: 'phs000007',
      label: 'Framingham Cohort',
      type: 'study',
      order: 'phs000007',
    })
  })

  it("orders variables by their study, so they sit next to it", () => {
    const order = (id: string) => toElements(CHD).find((e) => e.data.id === id)?.data.order
    expect(order('phv00001546')).toBe('phs000007 phv00001546') // FC219, Framingham
    const sorted = toElements(CHD)
      .filter((e) => e.data.type === 'variable')
      .map((e) => String(e.data.order))
      .sort()
    // Framingham's variables come first: phs000007 sorts before every other study
    expect(sorted.slice(0, 2).every((o) => o.startsWith('phs000007 '))).toBe(true)
  })

  it('weights variables by related_concepts_count, highest 1, lowest 0', () => {
    const weights = toElements(CHD)
      .filter((e) => e.data.type === 'variable')
      .map((e) => [e.data.label, e.data.weight])
    const w = Object.fromEntries(weights)
    expect(w['F33ANGHRTHSPDY']).toBe(1) // 35, the most
    expect(w['HEARTDIS']).toBe(0) // 9, the fewest
    expect(w['FC219']).toBeCloseTo((12 - 9) / (35 - 9))
    expect(toElements(CHD).find((e) => e.data.type === 'study')?.data.weight).toBeUndefined()
  })

  it('weights every variable 1 when the counts are equal or missing', () => {
    const g: KgGraph = {
      nodes: [
        { id: 'a', label: 'a', type: 'variable', related_concepts_count: 5 },
        { id: 'b', label: 'b', type: 'variable', related_concepts_count: 5 },
        { id: 'c', label: 'c', type: 'variable' },
      ],
      edges: [],
    }
    expect(toElements(g).map((e) => e.data.weight)).toEqual([1, 1, 1])
    // one missing count doesn't flatten the others
    g.nodes[1].related_concepts_count = 9
    expect(toElements(g).map((e) => e.data.weight)).toEqual([0, 1, 1])
  })

  it('gives edges stable ids', () => {
    const edge = toElements(CHD).find((e) => e.group === 'edges')!
    expect(edge.data.id).toBe(`${edge.data.source}->${edge.data.target}`)
  })
})
