import type { ElementDefinition } from 'cytoscape'

import type { KgGraph } from './types'

/**
 * Cytoscape elements for a graph. Each node's data carries `order`, the key the
 * radial layout sorts by within a ring: studies by id, variables by their study's
 * id, so a study's variables sit together, next to it. Variables also carry
 * `weight`: their related_concepts_count scaled to 0..1 within this graph (1 for
 * the highest; 1 when its count is missing or all counts are equal), drawn as opacity.
 */
export function toElements(graph: KgGraph): ElementDefinition[] {
  const studyOf = new Map<string, string>() // variable id → study id
  const types = new Map(graph.nodes.map((n) => [n.id, n.type]))
  for (const e of graph.edges) if (types.get(e.target) === 'study') studyOf.set(e.source, e.target)
  const order = (id: string, type: string) => (type === 'variable' ? `${studyOf.get(id) ?? ''} ${id}` : id)
  const counts = graph.nodes.flatMap((n) =>
    n.type === 'variable' && n.related_concepts_count !== undefined ? [n.related_concepts_count] : [],
  )
  const [lo, hi] = [Math.min(...counts), Math.max(...counts)]
  const weight = (count: number | undefined) =>
    count === undefined || !(hi > lo) ? 1 : (count - lo) / (hi - lo)
  return [
    ...graph.nodes.map((n) => ({
      group: 'nodes' as const,
      data: {
        id: n.id,
        label: n.label,
        type: n.type,
        order: order(n.id, n.type),
        ...(n.type === 'variable' && { weight: weight(n.related_concepts_count) }),
      },
    })),
    ...graph.edges.map((e) => ({
      group: 'edges' as const,
      data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target },
    })),
  ]
}
