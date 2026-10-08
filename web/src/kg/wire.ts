import type { KgEdge, KgGraph, KgNode, KgNodeType } from './types'

// The API's `kg` field (the `sources` and `done` events, POST /chat): one graph per
// tool call, attached by the Dug interceptor (examples/bdc/interceptors.py).

export type KgWireNode = {
  id: string
  name?: string
  type?: string // proposed: the node's role (concept | variable | study | term)
  category?: string // today: "Study" | "StudyVariable" for those, else the concept's category
  description?: string
  attributes?: { related_concepts_count?: number } & Record<string, unknown> // proposed
}

export type KgWireEdge = { subject: string; object: string; predicate?: string }

export type KgWireEntry = {
  tool: string
  args?: Record<string, unknown>
  label?: string // proposed: the graph in the user's terms
  nodes: KgWireNode[]
  edges: KgWireEdge[]
}

const TYPES: readonly string[] = ['concept', 'variable', 'study'] satisfies KgNodeType[]

/** The role of a wire node: its `type` if it names one we draw, else from today's
 * `category` ("Study", "StudyVariable"); anything else is a concept. */
function nodeType(n: KgWireNode): KgNodeType {
  if (n.type && TYPES.includes(n.type)) return n.type as KgNodeType
  if (n.category === 'Study') return 'study'
  if (n.category === 'StudyVariable') return 'variable'
  return 'concept'
}

/**
 * Merge the API's per-call graphs into one graph for the views: nodes by id (the
 * first call's fields win), edges by endpoints (one edge per pair; its predicate, if
 * any, from the first call). Concept–concept edges are kept. Returns null when there
 * is nothing to draw (no list, or no edges).
 */
export function fromKgList(value: unknown): KgGraph | null {
  if (!Array.isArray(value)) return null
  const nodes = new Map<string, KgNode>()
  const edges = new Map<string, KgEdge>()
  for (const entry of value as KgWireEntry[]) {
    for (const n of entry?.nodes ?? []) {
      if (!n?.id || nodes.has(n.id)) continue
      const type = nodeType(n)
      const node: KgNode = { id: n.id, label: n.name || n.id, type }
      if (type === 'concept' && n.category) node.concept_type = n.category
      const count = n.attributes?.related_concepts_count
      if (type === 'variable' && typeof count === 'number') node.related_concepts_count = count
      nodes.set(n.id, node)
    }
    for (const e of entry?.edges ?? []) {
      if (!e?.subject || !e.object) continue
      const key = `${e.subject}->${e.object}`
      if (!edges.has(key)) edges.set(key, { source: e.subject, target: e.object, ...(e.predicate && { predicate: e.predicate }) })
    }
  }
  // an edge to a node no call described (shouldn't happen): keep the graph consistent
  const kept = [...edges.values()].filter((e) => nodes.has(e.source) && nodes.has(e.target))
  return kept.length ? { nodes: [...nodes.values()], edges: kept } : null
}
