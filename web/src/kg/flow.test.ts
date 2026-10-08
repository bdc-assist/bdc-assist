import { describe, expect, it } from 'vitest'

import { collapseVersions } from './collapse'
import asthmaCopd from './fixtures/asthma-copd-graph.json'
import chd from './fixtures/chd-graph.json'
import { flowData } from './flow'
import type { KgGraph } from './types'

const CHD = collapseVersions(chd as KgGraph)
const TWO = collapseVersions(asthmaCopd as KgGraph)

describe('flowData', () => {
  it('one concept: a link to each study, as wide as its variables', () => {
    const { nodes, links } = flowData(CHD)
    expect(nodes.map((n) => n.node.type).filter((t) => t === 'study')).toHaveLength(7)
    expect(links).toHaveLength(7)
    expect(links.every((l) => l.source === 'MONDO:0005453')).toBe(true)
    expect(links.reduce((a, l) => a + l.variables.length, 0)).toBe(15)
    expect(links.find((l) => l.target === 'phs000007')?.variables.sort()).toEqual(['phv00001350', 'phv00001546'])
  })

  it('two concepts: shared studies get a link from each', () => {
    const { links } = flowData(TWO)
    const into = (study: string) => links.filter((l) => l.target === study).map((l) => l.source).sort()
    expect(into('phs000007')).toEqual(['MONDO:0004979', 'MONDO:0005002'])
    expect(into('phs000280')).toEqual(['MONDO:0004979', 'MONDO:0005002'])
    // every variable–concept edge is counted once
    const type = new Map(TWO.nodes.map((n) => [n.id, n.type]))
    const onConcepts = TWO.edges.filter((e) => type.get(e.target) === 'concept').length
    expect(links.reduce((a, l) => a + l.variables.length, 0)).toBe(onConcepts)
  })

  it('leaves out studies without variables and keeps concepts first', () => {
    const { nodes } = flowData({
      nodes: [
        { id: 'C', label: 'c', type: 'concept' },
        { id: 'v', label: 'v', type: 'variable' },
        { id: 'S', label: 's', type: 'study' },
        { id: 'empty', label: 'e', type: 'study' },
      ],
      edges: [
        { source: 'v', target: 'C' },
        { source: 'v', target: 'S' },
      ],
    })
    expect(nodes.map((n) => n.id)).toEqual(['C', 'S'])
  })
})
