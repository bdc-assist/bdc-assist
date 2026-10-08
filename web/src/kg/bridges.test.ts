import { describe, expect, it } from 'vitest'

import { bridges, sharedOnly } from './bridges'
import { collapseVersions } from './collapse'
import asthmaCopd from './fixtures/asthma-copd-graph.json'
import chd from './fixtures/chd-graph.json'
import type { KgGraph } from './types'

const TWO = collapseVersions(asthmaCopd as KgGraph)
const CHD = collapseVersions(chd as KgGraph)

describe('bridges', () => {
  it('finds the studies with variables on both concepts (real asthma + COPD graph)', () => {
    expect([...bridges(TWO)].sort()).toEqual(['phs000007', 'phs000280']) // Framingham, ARIC
  })

  it('finds none with a single concept', () => {
    expect(bridges(CHD).size).toBe(0)
  })

  it('counts a study whose releases only become shared once merged', () => {
    const g: KgGraph = {
      nodes: [
        { id: 'A', label: 'a', type: 'concept' },
        { id: 'B', label: 'b', type: 'concept' },
        { id: 'phv1.v1.p1', label: 'v1', type: 'variable' },
        { id: 'phv2.v1.p2', label: 'v2', type: 'variable' },
        { id: 'phs1.v1.p1', label: 's', type: 'study' },
        { id: 'phs1.v2.p2', label: 's', type: 'study' },
      ],
      edges: [
        { source: 'phv1.v1.p1', target: 'A' },
        { source: 'phv1.v1.p1', target: 'phs1.v1.p1' },
        { source: 'phv2.v1.p2', target: 'B' },
        { source: 'phv2.v1.p2', target: 'phs1.v2.p2' },
      ],
    }
    expect(bridges(g).size).toBe(0) // each release has one concept
    expect([...bridges(collapseVersions(g))]).toEqual(['phs1'])
  })

  it('counts a variable on two concepts, and its study', () => {
    const g: KgGraph = {
      nodes: [
        { id: 'A', label: 'a', type: 'concept' },
        { id: 'B', label: 'b', type: 'concept' },
        { id: 'v', label: 'copd or asthma', type: 'variable' },
        { id: 's', label: 's', type: 'study' },
      ],
      edges: [
        { source: 'v', target: 'A' },
        { source: 'v', target: 'B' },
        { source: 'v', target: 's' },
      ],
    }
    expect([...bridges(g)].sort()).toEqual(['s', 'v'])
  })

  it('does not count a concept related to other concepts', () => {
    const g: KgGraph = {
      nodes: [
        { id: 'A', label: 'a', type: 'concept' },
        { id: 'B', label: 'b', type: 'concept' },
        { id: 'C', label: 'c', type: 'concept' },
      ],
      edges: [
        { source: 'A', target: 'B', predicate: 'related_to' },
        { source: 'A', target: 'C', predicate: 'related_to' },
      ],
    }
    expect(bridges(g).size).toBe(0)
  })
})

describe('sharedOnly', () => {
  it('keeps the concepts, the shared studies and their variables', () => {
    const g = sharedOnly(TWO)
    const ids = (t: string) => g.nodes.filter((n) => n.type === t).map((n) => n.id).sort()
    expect(ids('concept')).toEqual(['MONDO:0004979', 'MONDO:0005002'])
    expect(ids('study')).toEqual(['phs000007', 'phs000280'])
    const variables = TWO.edges.filter((e) => e.target === 'phs000007' || e.target === 'phs000280').map((e) => e.source)
    expect(ids('variable')).toEqual(variables.sort())
    const kept = new Set(g.nodes.map((n) => n.id))
    expect(g.edges.every((e) => kept.has(e.source) && kept.has(e.target))).toBe(true)
  })

  it('leaves just the concepts when nothing is shared', () => {
    expect(sharedOnly(CHD).nodes.map((n) => n.type)).toEqual(['concept'])
    expect(sharedOnly(CHD).edges).toEqual([])
  })
})
