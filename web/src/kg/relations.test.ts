import { describe, expect, it } from 'vitest'

import { bridges } from './bridges'
import { connections } from './connections'
import { relations, toElements } from './elements'
import asthmaCopdKg from './fixtures/asthma-copd-kg.json'
import relatedKg from './fixtures/asthma-related-kg.json'
import { flowData } from './flow'
import { studyList } from './list'
import { fromKgList, kgParts } from './wire'

// real Dug results through the interceptor: asthma's concept graph, then its related
// concepts (get_concept_connections: concept → concept edges with predicates)
const KG = [asthmaCopdKg[0], ...relatedKg]
const G = fromKgList(KG)!
const ASTHMA = 'MONDO:0004979'

describe('concept–concept edges (real asthma data)', () => {
  it('are kept, with their predicates, and counted as relations', () => {
    // 50 connections, but epinephrine twice (ameliorates condition, applied to treat)
    expect(relations(G)).toHaveLength(49)
    expect(relations(G).every((e) => e.source === ASTHMA && e.predicates?.length)).toBe(true)
    expect(relations(G).find((e) => e.target === 'CHEBI:28918')?.predicates).toEqual([
      'ameliorates_condition',
      'applied_to_treat',
    ])
  })

  it('take a real name over a bare id when calls disagree', () => {
    // the connections call knows asthma only by id; the concept graph names it
    expect(fromKgList([...relatedKg, asthmaCopdKg[0]])!.nodes.find((n) => n.id === ASTHMA)?.label).toBe('asthma')
  })

  it('are marked as relations in the drawing, other edges not', () => {
    const edges = toElements(G).filter((e) => e.group === 'edges')
    const marked = edges.filter((e) => e.classes === 'relation')
    expect(marked).toHaveLength(49)
    expect(marked.find((e) => e.data.target === 'CHEBI:28918')?.data.predicate).toBe('ameliorates_condition / applied_to_treat')
    expect(edges.length - marked.length).toBe(G.edges.length - 49)
  })

  it('highlighting asthma lights its related concepts; one of them lights only asthma', () => {
    const related = relations(G)[0].target
    expect(connections(G, ASTHMA).nodes.has(related)).toBe(true)
    // no further through asthma: its variables and studies stay unlit
    expect([...connections(G, related).nodes].sort()).toEqual([ASTHMA, related].sort())
  })

  it("don't make bridges, flow bands or list rows", () => {
    expect(bridges(G).size).toBe(0)
    const flow = flowData(G)
    expect(flow.links.every((l) => !relations(G).some((r) => r.target === l.target))).toBe(true)
    const listed = new Set(studyList(G).flatMap((s) => s.concepts.map((c) => c.id)))
    expect(listed).toEqual(new Set([ASTHMA]))
  })

  it('label the connections call in plain words, naming asthma from the other call', () => {
    expect(kgParts(KG).map((p) => p.label)).toEqual(['asthma concept graph', 'asthma related concepts'])
    // on its own nothing names it, so the label keeps the id
    expect(kgParts(relatedKg).map((p) => p.label)).toEqual(['MONDO:0004979 related concepts'])
  })
})
