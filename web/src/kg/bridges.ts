import type { KgGraph } from './types'

/**
 * The nodes that connect two or more concepts: studies whose variables are on at
 * least two concepts between them (cohorts with data on both), and variables that
 * are themselves on two or more. Empty when the graph has fewer than two concepts.
 * Use it on the graph as drawn: merging releases can make a study a bridge.
 */
export function bridges(graph: KgGraph): Set<string> {
  const type = new Map(graph.nodes.map((n) => [n.id, n.type]))
  const conceptsOf = new Map<string, Set<string>>() // variable or study → its concepts
  const studyOf = new Map<string, string>()
  const add = (id: string, concept: string) => conceptsOf.set(id, (conceptsOf.get(id) ?? new Set()).add(concept))
  for (const e of graph.edges) {
    if (type.get(e.target) === 'concept') add(e.source, e.target)
    if (type.get(e.target) === 'study') studyOf.set(e.source, e.target)
  }
  for (const [variable, study] of studyOf) for (const c of conceptsOf.get(variable) ?? []) add(study, c)
  return new Set([...conceptsOf].filter(([, cs]) => cs.size >= 2).map(([id]) => id))
}

/**
 * Only what the concepts share: the concepts, the bridge studies, all of their
 * variables, and any bridge variable; studies on a single concept and their
 * variables go. With no bridges, just the concepts are left.
 */
export function sharedOnly(graph: KgGraph): KgGraph {
  const shared = bridges(graph)
  const type = new Map(graph.nodes.map((n) => [n.id, n.type]))
  const studyOf = new Map(graph.edges.filter((e) => type.get(e.target) === 'study').map((e) => [e.source, e.target]))
  const keep = (id: string) => {
    const t = type.get(id)
    if (t === 'concept' || shared.has(id)) return true
    return t === 'variable' && shared.has(studyOf.get(id) ?? '')
  }
  return {
    nodes: graph.nodes.filter((n) => keep(n.id)),
    edges: graph.edges.filter((e) => keep(e.source) && keep(e.target)),
  }
}
