import { connections, type Connections } from './connections'
import type { KgGraph, KgNode } from './types'

/**
 * What the user picked, the same in every view: a node, or a concept × study pair
 * ("what does this study have on this concept?" — a band in the flow view).
 */
export type KgFocus = { node: string } | { concept: string; study: string }

export const isPair = (f: KgFocus): f is { concept: string; study: string } => 'concept' in f

export const sameFocus = (a: KgFocus | null, b: KgFocus | null) =>
  JSON.stringify(a) === JSON.stringify(b)

/** The variables of `study` that are on `concept`, in graph order. */
export function pairVariables(graph: KgGraph, concept: string, study: string): KgNode[] {
  const on = (target: string) => new Set(graph.edges.filter((e) => e.target === target).map((e) => e.source))
  const [onConcept, inStudy] = [on(concept), on(study)]
  return graph.nodes.filter((n) => n.type === 'variable' && onConcept.has(n.id) && inStudy.has(n.id))
}

/**
 * What to light up for a focus: a node's connections (see connections), or for a
 * pair the concept, the study and the variables between them, with their edges.
 * Edge ids are "source->target", like toElements. Empty if it isn't in the graph.
 */
export function focusConnections(graph: KgGraph, focus: KgFocus): Connections {
  if (!isPair(focus)) return connections(graph, focus.node)
  const nodes = new Set<string>()
  const edges = new Set<string>()
  const variables = pairVariables(graph, focus.concept, focus.study)
  if (!variables.length) return { nodes, edges }
  nodes.add(focus.concept).add(focus.study)
  for (const v of variables) {
    nodes.add(v.id)
    edges.add(`${v.id}->${focus.concept}`).add(`${v.id}->${focus.study}`)
  }
  return { nodes, edges }
}
