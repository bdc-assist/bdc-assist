// The knowledge graph the views draw: the API's per-call `kg` graphs merged into one
// (see fromKgList in wire.ts). Everything under src/kg/ is self-contained: no imports
// from the rest of the app, no React, so it can move into another host system as-is.

export type KgNodeType = 'concept' | 'variable' | 'study'

export type KgNode = {
  id: string // concept id, dbGaP variable accession, or dbGaP study accession (with version)
  label: string
  type: KgNodeType
  concept_type?: string // concepts: Dug's category, verbatim
  related_concepts_count?: number // variables: other concepts it links to (Dug: rough relevance signal)
  versions?: string[] // after collapseVersions: the versioned ids merged into this node
}

/** variable → concept, variable → study, and concept → concept (with Dug's predicate,
 * e.g. from get_concept_connections). */
export type KgEdge = { source: string; target: string; predicate?: string }

export type KgGraph = { nodes: KgNode[]; edges: KgEdge[] }
