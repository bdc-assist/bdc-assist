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

  it('gives edges stable ids', () => {
    const edge = toElements(CHD).find((e) => e.group === 'edges')!
    expect(edge.data.id).toBe(`${edge.data.source}->${edge.data.target}`)
  })
})
