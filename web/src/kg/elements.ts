import type { ElementDefinition } from 'cytoscape'

import type { KgGraph } from './types'

/** Each variable's related_concepts_count scaled to 0..1 within the graph: 1 for the
 * highest, 0 for the lowest; 1 when its count is missing or all counts are equal. */
export function variableWeights(graph: KgGraph): Map<string, number> {
  const counts = graph.nodes.flatMap((n) =>
    n.type === 'variable' && n.related_concepts_count !== undefined ? [n.related_concepts_count] : [],
  )
  const [lo, hi] = [Math.min(...counts), Math.max(...counts)]
  return new Map(
    graph.nodes
      .filter((n) => n.type === 'variable')
      .map((n) => {
        const c = n.related_concepts_count
        return [n.id, c === undefined || !(hi > lo) ? 1 : (c - lo) / (hi - lo)]
      }),
  )
}

/**
 * Cytoscape elements for a graph. Each node's data carries `order`, the key the
 * radial layout sorts by within a ring: studies by id, variables by their study's
 * id, so a study's variables sit together, next to it. Variables also carry
 * `weight` (variableWeights), drawn as a light-to-dark colour.
 */
export function toElements(graph: KgGraph): ElementDefinition[] {
  const studyOf = new Map<string, string>() // variable id → study id
  const types = new Map(graph.nodes.map((n) => [n.id, n.type]))
  for (const e of graph.edges) if (types.get(e.target) === 'study') studyOf.set(e.source, e.target)
  const order = (id: string, type: string) => (type === 'variable' ? `${studyOf.get(id) ?? ''} ${id}` : id)
  const weights = variableWeights(graph)
  return [
    ...graph.nodes.map((n) => ({
      group: 'nodes' as const,
      data: {
        id: n.id,
        label: n.label,
        type: n.type,
        order: order(n.id, n.type),
        ...(n.type === 'variable' && { weight: weights.get(n.id) }),
      },
    })),
    ...graph.edges.map((e) => ({
      group: 'edges' as const,
      data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target },
    })),
  ]
}
