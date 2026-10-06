import { describe, expect, it } from 'vitest'

import { collapseVersions } from './collapse'
import chd from './fixtures/chd-graph.json'
import { conceptLink, nodeLinks } from './links'
import type { KgGraph } from './types'

const RAW = chd as KgGraph
const COLLAPSED = collapseVersions(RAW)
const DBGAP = 'https://www.ncbi.nlm.nih.gov/projects/gap/cgi-bin'
const node = (g: KgGraph, id: string) => g.nodes.find((n) => n.id === id)!

describe('nodeLinks', () => {
  it('links a study release to its study page', () => {
    expect(nodeLinks(node(RAW, 'phs000007.v34.p15'), RAW)).toEqual([
      { label: 'phs000007.v34.p15', url: `${DBGAP}/study.cgi?study_id=phs000007.v34.p15` },
    ])
  })

  it('links every release of a collapsed study', () => {
    expect(nodeLinks(node(COLLAPSED, 'phs000007'), COLLAPSED).map((l) => l.label)).toEqual([
      'phs000007.v34.p15',
      'phs000007.v31.p12',
    ])
  })

  it('links a variable to its page in its study release', () => {
    expect(nodeLinks(node(RAW, 'phv00001546.v1.p12'), RAW)).toEqual([
      { label: 'phv00001546.v1.p12', url: `${DBGAP}/variable.cgi?study_id=phs000007.v31.p12&phv=1546` },
    ])
  })

  it('pairs collapsed variable and study releases by participant set', () => {
    expect(nodeLinks(node(COLLAPSED, 'phv00001546'), COLLAPSED)).toEqual([
      { label: 'phv00001546.v1.p15', url: `${DBGAP}/variable.cgi?study_id=phs000007.v34.p15&phv=1546` },
      { label: 'phv00001546.v1.p12', url: `${DBGAP}/variable.cgi?study_id=phs000007.v31.p12&phv=1546` },
    ])
  })

  it('links a concept to its ontology page', () => {
    expect(nodeLinks(node(RAW, 'MONDO:0005453'), RAW)).toEqual([
      { label: 'MONDO:0005453', url: 'https://purl.obolibrary.org/obo/MONDO_0005453' },
    ])
  })
})

describe('conceptLink', () => {
  it('uses the right provider per prefix', () => {
    expect(conceptLink('EFO:1002011')?.url).toBe('https://www.ebi.ac.uk/ols4/ontologies/efo/classes?obo_id=EFO%3A1002011')
    expect(conceptLink('UMLS:C0155886')).toEqual({
      label: 'UMLS:C0155886 (UMLS login)',
      url: 'https://uts.nlm.nih.gov/uts/umls/concept/C0155886',
    })
    // any prefix Dug hasn't returned yet
    expect(conceptLink('HP:0001903')?.url).toBe('https://bioregistry.io/HP%3A0001903')
    expect(conceptLink('NCBIGene:3043')?.url).toBe('https://bioregistry.io/NCBIGene%3A3043')
  })

  it('needs a prefix', () => {
    expect(conceptLink('asthma')).toBeNull()
    expect(conceptLink(':123')).toBeNull()
  })
})
