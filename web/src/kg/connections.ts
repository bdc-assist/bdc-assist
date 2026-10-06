import type { KgGraph } from './types'

export type Connections = { nodes: Set<string>; edges: Set<string> } // edge ids: "source->target"

/**
 * What a node is connected to, for highlighting: its direct neighbours, and one
 * step further through variables only. A concept links to everything, so going
 * on through concepts or studies would light up the whole graph; going through
 * variables gives the useful picture:
 * - a study: its variables, and the concepts they're on
 * - a concept: its variables, and their studies
 * - a variable: its concept(s) and its study
 * Includes the node itself; empty for an id not in the graph.
 */
export function connections(graph: KgGraph, id: string): Connections {
  const type = new Map(graph.nodes.map((n) => [n.id, n.type]))
  const nodes = new Set<string>()
  const edges = new Set<string>()
  if (!type.has(id)) return { nodes, edges }
  const around = new Map<string, { other: string; edge: string }[]>()
  for (const e of graph.edges) {
    const edge = `${e.source}->${e.target}`
    around.set(e.source, [...(around.get(e.source) ?? []), { other: e.target, edge }])
    around.set(e.target, [...(around.get(e.target) ?? []), { other: e.source, edge }])
  }
  nodes.add(id)
  for (const { other, edge } of around.get(id) ?? []) {
    nodes.add(other)
    edges.add(edge)
    if (type.get(id) === 'variable' || type.get(other) !== 'variable') continue
    for (const next of around.get(other) ?? []) {
      nodes.add(next.other)
      edges.add(next.edge)
    }
  }
  return { nodes, edges }
}
