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
 * first call's fields win, except that a real name beats a bare id), edges by endpoints (one edge per pair, keeping
 * all its predicates). Concept–concept edges are kept. Returns null when there
 * is nothing to draw (no list, or no edges).
 */
export function fromKgList(value: unknown): KgGraph | null {
  if (!Array.isArray(value)) return null
  const nodes = new Map<string, KgNode>()
  const edges = new Map<string, KgEdge>()
  for (const entry of value as KgWireEntry[]) {
    for (const n of entry?.nodes ?? []) {
      if (!n?.id) continue
      const seen = nodes.get(n.id)
      if (seen) {
        // a call may know a node only by id (get_concept_connections names the
        // neighbours, not the concept asked about): take a real name from another
        if (seen.label === n.id && n.name && n.name !== n.id) seen.label = n.name
        continue
      }
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
      const edge = edges.get(key) ?? { source: e.subject, target: e.object }
      // one edge per pair; every way they're related is kept on it
      if (e.predicate && !edge.predicates?.includes(e.predicate)) edge.predicates = [...(edge.predicates ?? []), e.predicate]
      edges.set(key, edge)
    }
  }
  // an edge to a node no call described (shouldn't happen): keep the graph consistent
  const kept = [...edges.values()].filter((e) => nodes.has(e.source) && nodes.has(e.target))
  return kept.length ? { nodes: [...nodes.values()], edges: kept } : null
}

export type KgPart = { label: string; graph: KgGraph } // one tool call's graph, for showing it on its own

const words = (v: unknown) => (Array.isArray(v) ? v : [v]).filter((x) => typeof x === 'string' && x).join(' + ')

/**
 * A graph entry in the user's terms, never a tool name: its `label` if the server
 * sent one, else made from the tool and its arguments ("asthma concept graph",
 * "asthma + COPD cohort variables"); a concept id is named from the entry's nodes.
 */
export function kgLabel(entry: KgWireEntry): string {
  if (entry.label) return entry.label
  const args = entry.args ?? {}
  const conceptName = (id: unknown) =>
    (typeof id === 'string' && entry.nodes?.find((n) => n.id === id)?.name) || (typeof id === 'string' ? id : '')
  const what = (() => {
    switch (entry.tool) {
      case 'get_concept_graph':
        return `${conceptName(args.concept_id)} concept graph`
      case 'get_concept_connections':
        return `${conceptName(args.concept_id)} related concepts`
      case 'find_cohort_variables':
        return `${words(args.concepts)} cohort variables`
      case 'search_concepts':
        return `${words(args.search_term)} concept search`
      case 'picsure_search':
        return `${words(Object.values(args).find((v) => typeof v === 'string'))} PIC-SURE variables`
      default:
        return entry.tool?.replace(/_/g, ' ') ?? 'graph'
    }
  })()
  return what.trim() || (entry.tool ?? 'graph').replace(/_/g, ' ')
}

/** Each entry of the API's `kg` that has something to draw, labelled (kgLabel). */
export function kgParts(value: unknown): KgPart[] {
  if (!Array.isArray(value)) return []
  return (value as KgWireEntry[]).flatMap((entry) => {
    const graph = fromKgList([entry])
    return graph ? [{ label: kgLabel(entry), graph }] : []
  })
}
