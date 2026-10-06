import { variableWeights } from './elements'
import type { KgGraph, KgNode } from './types'

export type ListVariable = {
  variable: KgNode
  concepts: KgNode[] // the concepts it's linked to
  weight: number // variableWeights: 0..1, higher = more related concepts
}

export type ListStudy = {
  study: KgNode | null // null: variables with no study
  concepts: KgNode[] // every concept its variables are linked to, in graph order
  variables: ListVariable[] // by label, then id
}

/**
 * The graph as a list, grouped by study: each study with the concepts it covers
 * and its variables. Studies covering more concepts come first, then by name, so
 * with several concepts the shared studies lead. For a list or table view, or a
 * text alternative to the graph.
 */
export function studyList(graph: KgGraph): ListStudy[] {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]))
  const conceptOrder = new Map(graph.nodes.filter((n) => n.type === 'concept').map((n, i) => [n.id, i]))
  const weights = variableWeights(graph)
  const studyOf = new Map<string, string>()
  const conceptsOf = new Map<string, KgNode[]>()
  for (const e of graph.edges) {
    const target = byId.get(e.target)
    if (target?.type === 'study') studyOf.set(e.source, e.target)
    if (target?.type === 'concept') conceptsOf.set(e.source, [...(conceptsOf.get(e.source) ?? []), target])
  }
  const inOrder = (cs: KgNode[]) => [...new Set(cs)].sort((a, b) => conceptOrder.get(a.id)! - conceptOrder.get(b.id)!)

  const groups = new Map<string | null, ListVariable[]>()
  for (const v of graph.nodes) {
    if (v.type !== 'variable') continue
    const key = studyOf.get(v.id) ?? null
    const item = { variable: v, concepts: inOrder(conceptsOf.get(v.id) ?? []), weight: weights.get(v.id) ?? 1 }
    groups.set(key, [...(groups.get(key) ?? []), item])
  }
  const label = (n: KgNode | null) => n?.label ?? ''
  return [...groups]
    .map(([id, variables]) => ({
      study: id === null ? null : byId.get(id)!,
      concepts: inOrder(variables.flatMap((v) => v.concepts)),
      variables: variables.sort(
        (a, b) => a.variable.label.localeCompare(b.variable.label) || a.variable.id.localeCompare(b.variable.id),
      ),
    }))
    .sort(
      (a, b) =>
        Number(a.study === null) - Number(b.study === null) ||
        b.concepts.length - a.concepts.length ||
        label(a.study).localeCompare(label(b.study)),
    )
}
