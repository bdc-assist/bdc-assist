import type { KgGraph, KgNode } from './types'

// dbGaP accessions end in ".v<version>.p<participant set>": phs000007.v34.p15, phv00001546.v1.p12
const VERSION = /\.v\d+\.p\d+$/

/** The accession without its version: "phs000007.v34.p15" → "phs000007". Ids
 * without a version (concepts) come back unchanged. */
export const baseId = (id: string) => id.replace(VERSION, '')

/**
 * Merge the releases of the same study or variable into one node, e.g. Framingham
 * phs000007.v31.p12 and phs000007.v34.p15 → phs000007. A merged node takes the
 * base id, the first release's label and fields, and lists every versioned id it
 * stands for in `versions` (first-seen order), so links to a specific release
 * stay possible. Every study and variable gets `versions`, even with one release.
 * Concepts are left alone. Edges are re-pointed and deduplicated. Pure: the input
 * is not modified.
 */
export function collapseVersions(graph: KgGraph): KgGraph {
  const nodes = new Map<string, KgNode>()
  const idOf = new Map<string, string>() // original id → collapsed id
  for (const n of graph.nodes) {
    const id = n.type === 'concept' ? n.id : baseId(n.id)
    idOf.set(n.id, id)
    const merged = nodes.get(id)
    if (!merged) nodes.set(id, n.type === 'concept' ? { ...n } : { ...n, id, versions: [n.id] })
    else if (merged.versions && !merged.versions.includes(n.id)) merged.versions.push(n.id)
  }
  const edges = new Map<string, KgGraph['edges'][number]>()
  for (const e of graph.edges) {
    const source = idOf.get(e.source) ?? e.source
    const target = idOf.get(e.target) ?? e.target
    edges.set(JSON.stringify([source, target]), { source, target })
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] }
}
