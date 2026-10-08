import type { KgGraph } from './types'

export type Position = { x: number; y: number }

export const COLUMN_GAP = 260 // between the concept, variable and study columns
export const ROW = 18 // between variables

/**
 * A left-to-right flow: concepts | variables | studies. Variables are stacked in
 * one column, grouped by study; studies and concepts sit level with the middle of
 * their variables, so edges run mostly straight. Studies are ordered by the
 * concepts they connect to (in graph order), so with several concepts the studies
 * they share end up between the ones each has alone.
 */
export function columnPositions(graph: KgGraph): Map<string, Position> {
  const type = new Map(graph.nodes.map((n) => [n.id, n.type]))
  const conceptIndex = new Map(graph.nodes.filter((n) => n.type === 'concept').map((n, i) => [n.id, i]))
  const studyOf = new Map<string, string>()
  const conceptsOf = new Map<string, number[]>() // variable → its concepts' indexes
  for (const e of graph.edges) {
    if (type.get(e.target) === 'study') studyOf.set(e.source, e.target)
    const ci = conceptIndex.get(e.target)
    if (ci !== undefined) conceptsOf.set(e.source, [...(conceptsOf.get(e.source) ?? []), ci])
  }
  const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0)
  const variables = graph.nodes.filter((n) => n.type === 'variable').map((n) => n.id)
  const variableKey = new Map(variables.map((v) => [v, mean(conceptsOf.get(v) ?? [])]))
  const studyKey = new Map<string, number>()
  for (const s of new Set(studyOf.values())) {
    studyKey.set(s, mean(variables.filter((v) => studyOf.get(v) === s).map((v) => variableKey.get(v)!)))
  }
  const byKey = (key: Map<string, number>) => (a: string, b: string) =>
    (key.get(a) ?? Infinity) - (key.get(b) ?? Infinity) || a.localeCompare(b)
  const studies = [...studyKey.keys()].sort(byKey(studyKey))
  const rank = new Map(studies.map((s, i) => [s, i]))
  variables.sort(
    (a, b) =>
      (rank.get(studyOf.get(a) ?? '') ?? Infinity) - (rank.get(studyOf.get(b) ?? '') ?? Infinity) ||
      byKey(variableKey)(a, b),
  )

  const pos = new Map<string, Position>()
  variables.forEach((v, i) => pos.set(v, { x: COLUMN_GAP, y: i * ROW }))
  const level = (members: string[]) => mean(members.map((m) => pos.get(m)!.y))
  for (const s of studies) pos.set(s, { x: 2 * COLUMN_GAP, y: level(variables.filter((v) => studyOf.get(v) === s)) })
  // concepts at the middle of their variables, pushed apart if they'd overlap
  const concepts = [...conceptIndex.keys()]
    .map((c) => ({ c, y: level(variables.filter((v) => conceptsOf.get(v)?.includes(conceptIndex.get(c)!))) }))
    .sort((a, b) => a.y - b.y)
  concepts.forEach((c, i) => {
    const y = i > 0 ? Math.max(c.y, pos.get(concepts[i - 1].c)!.y + 3 * ROW) : c.y
    pos.set(c.c, { x: 0, y })
  })
  // anything left (no edges): below the rest, in its own column
  let y = variables.length * ROW
  for (const n of graph.nodes) {
    if (pos.has(n.id)) continue
    const x = n.type === 'concept' ? 0 : n.type === 'variable' ? COLUMN_GAP : 2 * COLUMN_GAP
    pos.set(n.id, { x, y: (y += ROW) })
  }
  return pos
}
