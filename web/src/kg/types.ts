// The knowledge graph BDC Assist sends: the `graph` SSE event and the `graph`
// field of done and POST /chat (see _knowledge_graph in bdc_assist/graph.py).
// Everything under src/kg/ is self-contained: no imports from the rest of the
// app, no React, so it can move into another host system as-is.

export type KgNodeType = 'concept' | 'variable' | 'study'

export type KgNode = {
  id: string // concept id, dbGaP variable accession, or dbGaP study accession (with version)
  label: string
  type: KgNodeType
  concept_type?: string // concepts: Dug's category, verbatim
  related_concepts_count?: number // variables: other concepts it links to (Dug: rough relevance signal)
  versions?: string[] // after collapseVersions: the versioned ids merged into this node
}

/** Untyped: variable → concept, variable → study. */
export type KgEdge = { source: string; target: string }

export type KgGraph = { nodes: KgNode[]; edges: KgEdge[] }

/** The server sends {} for "no graph"; normalize that (and junk) to null. */
export function asGraph(value: unknown): KgGraph | null {
  const g = value as Partial<KgGraph> | null | undefined
  return Array.isArray(g?.nodes) && g.nodes.length > 0 && Array.isArray(g.edges) ? (g as KgGraph) : null
}
