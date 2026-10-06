import { describe, expect, it } from 'vitest'

import { collapseVersions } from './collapse'
import { connections } from './connections'
import asthmaCopd from './fixtures/asthma-copd-graph.json'
import type { KgGraph } from './types'

const TWO = collapseVersions(asthmaCopd as KgGraph)
const ASTHMA = 'MONDO:0004979'
const COPD = 'MONDO:0005002'
const type = new Map(TWO.nodes.map((n) => [n.id, n.type]))
const ofType = (ids: Set<string>, t: string) => [...ids].filter((id) => type.get(id) === t)
const variablesOf = (study: string) => TWO.edges.filter((e) => e.target === study).map((e) => e.source)

describe('connections', () => {
  it('a shared study: its variables and both concepts', () => {
    const c = connections(TWO, 'phs000007') // Framingham
    expect(ofType(c.nodes, 'concept').sort()).toEqual([ASTHMA, COPD])
    expect(ofType(c.nodes, 'variable').sort()).toEqual(variablesOf('phs000007').sort())
    expect(ofType(c.nodes, 'study')).toEqual(['phs000007']) // not other studies
  })

  it('a concept: its variables and their studies, not the other concept', () => {
    const c = connections(TWO, COPD)
    expect(ofType(c.nodes, 'concept')).toEqual([COPD])
    const vars = TWO.edges.filter((e) => e.target === COPD).map((e) => e.source)
    expect(ofType(c.nodes, 'variable').sort()).toEqual(vars.sort())
    expect(ofType(c.nodes, 'study')).toContain('phs000007')
  })

  it('a variable: just its concept and its study', () => {
    const v = variablesOf('phs000280')[0] // an ARIC variable
    const c = connections(TWO, v)
    expect(c.nodes.size).toBe(3)
    expect(ofType(c.nodes, 'study')).toEqual(['phs000280'])
    expect(c.edges.size).toBe(2)
  })

  it('names edges like toElements does', () => {
    const v = variablesOf('phs000280')[0]
    expect(connections(TWO, v).edges).toContain(`${v}->phs000280`)
  })

  it('is empty for an unknown id', () => {
    expect(connections(TWO, 'nope').nodes.size).toBe(0)
  })
})
