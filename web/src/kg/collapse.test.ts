import { describe, expect, it } from 'vitest'

import { baseId, collapseVersions } from './collapse'
import asthmaCopd from './fixtures/asthma-copd-graph.json'
import chd from './fixtures/chd-graph.json'
import type { KgGraph } from './types'

// real server output for Dug get_concept_graph on congenital heart disease (MONDO:0005453),
// made from tests/fixtures/dug_concept_graph_chd.json; the same graph fromKgList makes of chd-kg.json
const CHD = chd as KgGraph

const count = (g: KgGraph, type: string) => g.nodes.filter((n) => n.type === type).length

describe('baseId', () => {
  it('strips the dbGaP version', () => {
    expect(baseId('phs000007.v34.p15')).toBe('phs000007')
    expect(baseId('phv00001546.v1.p12')).toBe('phv00001546')
  })

  it('leaves ids without one alone', () => {
    expect(baseId('MONDO:0005453')).toBe('MONDO:0005453')
    expect(baseId('phs000007')).toBe('phs000007')
  })
})

describe('collapseVersions', () => {
  it('merges releases in the real CHD graph', () => {
    expect([count(CHD, 'variable'), count(CHD, 'study'), CHD.edges.length]).toEqual([17, 8, 34])
    const g = collapseVersions(CHD)
    // FC219 and FB230 each appear in two Framingham releases; Framingham itself in two
    expect([count(g, 'concept'), count(g, 'variable'), count(g, 'study')]).toEqual([1, 15, 7])
    expect(g.edges).toHaveLength(30) // each variable: one edge to the concept, one to its study
    expect(g.nodes.find((n) => n.id === 'phs000007')).toEqual({
      id: 'phs000007',
      label: 'Framingham Cohort',
      type: 'study',
      versions: ['phs000007.v34.p15', 'phs000007.v31.p12'],
    })
    expect(g.nodes.find((n) => n.id === 'phv00001546')?.versions).toEqual([
      'phv00001546.v1.p15',
      'phv00001546.v1.p12',
    ])
  })

  it('keeps same-named variables with different accessions apart', () => {
    // WHI has F33ANGHRTHSP twice: phv00202373 and phv00202361
    const g = collapseVersions(CHD)
    expect(g.nodes.filter((n) => n.label === 'F33ANGHRTHSP').map((n) => n.id)).toEqual([
      'phv00202373',
      'phv00202361',
    ])
  })

  it('gives every study and variable its versions, and concepts none', () => {
    const g = collapseVersions(CHD)
    for (const n of g.nodes) {
      if (n.type === 'concept') expect(n.versions).toBeUndefined()
      else expect(n.versions?.length).toBeGreaterThan(0)
    }
  })

  it('re-points and deduplicates edges', () => {
    const g = collapseVersions({
      nodes: [
        { id: 'C', label: 'c', type: 'concept' },
        { id: 'phv1.v1.p1', label: 'V', type: 'variable' },
        { id: 'phv1.v1.p2', label: 'V', type: 'variable' },
        { id: 'phs1.v1.p1', label: 'S', type: 'study' },
        { id: 'phs1.v2.p2', label: 'S', type: 'study' },
      ],
      edges: [
        { source: 'phv1.v1.p1', target: 'C' },
        { source: 'phv1.v1.p1', target: 'phs1.v1.p1' },
        { source: 'phv1.v1.p2', target: 'C' },
        { source: 'phv1.v1.p2', target: 'phs1.v2.p2' },
      ],
    })
    expect(g.edges).toEqual([
      { source: 'phv1', target: 'C' },
      { source: 'phv1', target: 'phs1' },
    ])
  })

  it('joins two concepts through their shared studies (real asthma + COPD graph)', () => {
    // two get_concept_graph calls, merged by the server: no variable is on both concepts,
    // but Framingham and ARIC have variables on each
    const g = collapseVersions(asthmaCopd as KgGraph)
    expect([count(g, 'concept'), count(g, 'variable'), count(g, 'study')]).toEqual([2, 67, 19])
    const type = new Map(g.nodes.map((n) => [n.id, n.type]))
    const conceptsOf = new Map<string, Set<string>>() // study → concepts its variables are on
    const studyOf = new Map(g.edges.filter((e) => type.get(e.target) === 'study').map((e) => [e.source, e.target]))
    for (const e of g.edges) {
      if (type.get(e.target) !== 'concept') continue
      const study = studyOf.get(e.source)!
      conceptsOf.set(study, (conceptsOf.get(study) ?? new Set()).add(e.target))
    }
    const shared = [...conceptsOf].filter(([, c]) => c.size === 2).map(([s]) => s)
    expect(shared.sort()).toEqual(['phs000007', 'phs000280'])
  })

  it('does not modify its input', () => {
    const before = JSON.stringify(CHD)
    collapseVersions(CHD)
    expect(JSON.stringify(CHD)).toBe(before)
  })
})
