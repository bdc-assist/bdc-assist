import { describe, expect, it } from 'vitest'

import { collapseVersions } from './collapse'
import asthmaCopd from './fixtures/asthma-copd-graph.json'
import { focusConnections, pairVariables, sameFocus } from './focus'
import type { KgGraph } from './types'

const TWO = collapseVersions(asthmaCopd as KgGraph)
const ASTHMA = 'MONDO:0004979'
const COPD = 'MONDO:0005002'
const FHS = 'phs000007'
const on = (target: string) => new Set(TWO.edges.filter((e) => e.target === target).map((e) => e.source))

describe('pairVariables', () => {
  it("is the study's variables on that concept, and only those", () => {
    const vars = pairVariables(TWO, ASTHMA, FHS).map((v) => v.id)
    expect(vars.length).toBeGreaterThan(0)
    expect(vars.every((v) => on(ASTHMA).has(v) && on(FHS).has(v))).toBe(true)
    const both = [...on(FHS)].filter((v) => on(ASTHMA).has(v) || on(COPD).has(v))
    expect(vars.length + pairVariables(TWO, COPD, FHS).length).toBe(both.length) // no variable is on both here
  })
})

describe('focusConnections', () => {
  it('a node: its connections', () => {
    expect(focusConnections(TWO, { node: FHS }).nodes).toContain(COPD) // a shared study reaches both concepts
  })

  it('a pair: the concept, the study and the variables between them, not the other concept', () => {
    const c = focusConnections(TWO, { concept: ASTHMA, study: FHS })
    const vars = pairVariables(TWO, ASTHMA, FHS).map((v) => v.id)
    expect([...c.nodes].sort()).toEqual([ASTHMA, FHS, ...vars].sort())
    expect(c.nodes.has(COPD)).toBe(false)
    expect(c.edges.size).toBe(2 * vars.length)
    expect(c.edges).toContain(`${vars[0]}->${ASTHMA}`)
  })

  it('a pair with nothing between them is empty', () => {
    const onlyCopd = [...new Set(TWO.edges.map((e) => e.target))].find(
      (s) => s.startsWith('phs') && pairVariables(TWO, ASTHMA, s).length === 0,
    )!
    expect(focusConnections(TWO, { concept: ASTHMA, study: onlyCopd }).nodes.size).toBe(0)
  })
})

describe('sameFocus', () => {
  it('compares by value', () => {
    expect(sameFocus({ concept: 'a', study: 'b' }, { concept: 'a', study: 'b' })).toBe(true)
    expect(sameFocus({ node: 'a' }, { concept: 'a', study: 'b' })).toBe(false)
    expect(sameFocus(null, null)).toBe(true)
  })
})
