import { describe, expect, it } from 'vitest'

import asthmaCopdKg from './fixtures/asthma-copd-kg.json'
import asthmaCopd from './fixtures/asthma-copd-graph.json'
import chdKg from './fixtures/chd-kg.json'
import chd from './fixtures/chd-graph.json'
import type { KgGraph } from './types'
import { fromKgList } from './wire'

// *-kg.json: what the API sends today, made by main's interceptor (to_kg in
// examples/bdc/interceptors.py) from the same Dug results as the *-graph.json fixtures
const count = (g: KgGraph, t: string) => g.nodes.filter((n) => n.type === t).length

describe('fromKgList', () => {
  it("merges the interceptor's per-call graphs into the same graph as before", () => {
    for (const [wire, before] of [
      [chdKg, chd],
      [asthmaCopdKg, asthmaCopd],
    ] as const) {
      const g = fromKgList(wire)!
      const b = before as KgGraph
      expect(['concept', 'variable', 'study'].map((t) => count(g, t))).toEqual(
        ['concept', 'variable', 'study'].map((t) => count(b, t)),
      )
      const edges = (x: KgGraph) => x.edges.map((e) => `${e.source}->${e.target}`).sort()
      expect(edges(g)).toEqual(edges(b))
    }
  })

  it("takes the role from today's category, the label from name", () => {
    const g = fromKgList(asthmaCopdKg)!
    expect(g.nodes.find((n) => n.id === 'phs000007.v34.p15')).toEqual({
      id: 'phs000007.v34.p15',
      label: 'Framingham Cohort',
      type: 'study',
    })
    expect(g.nodes.find((n) => n.id === 'MONDO:0004979')).toMatchObject({ type: 'concept', concept_type: 'NamedThing' })
  })

  it('takes the proposed fields too: type, attributes.related_concepts_count, predicate', () => {
    const g = fromKgList([
      {
        tool: 'get_concept_graph',
        nodes: [
          { id: 'C', name: 'c', type: 'concept', category: 'biolink:Disease' },
          { id: 'D', name: 'd', type: 'concept' },
          { id: 'v', name: 'V', type: 'variable', attributes: { related_concepts_count: 9 } },
        ],
        edges: [
          { subject: 'v', object: 'C' },
          { subject: 'C', object: 'D', predicate: 'related_to' },
        ],
      },
    ])!
    expect(g.nodes).toEqual([
      { id: 'C', label: 'c', type: 'concept', concept_type: 'biolink:Disease' },
      { id: 'D', label: 'd', type: 'concept' },
      { id: 'v', label: 'V', type: 'variable', related_concepts_count: 9 },
    ])
    expect(g.edges).toContainEqual({ source: 'C', target: 'D', predicate: 'related_to' })
  })

  it('is null for no list, an empty list, or graphs without edges', () => {
    expect(fromKgList(undefined)).toBeNull()
    expect(fromKgList({})).toBeNull()
    expect(fromKgList([])).toBeNull()
    expect(fromKgList([{ tool: 't', nodes: [{ id: 'a' }], edges: [] }])).toBeNull()
  })
})
