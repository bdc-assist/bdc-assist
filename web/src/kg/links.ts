import type { KgGraph, KgNode } from './types'

export type KgLink = { label: string; url: string }

const DBGAP = 'https://www.ncbi.nlm.nih.gov/projects/gap/cgi-bin'

// Sources whose pages need an account (UTS, BioPortal, GtoPdb): the link says so
const LOGIN = new Set(['UMLS', 'SNOMEDCT', 'MEDDRA', 'GTOPDB'])

/** The page for a concept id (CURIE). Dug's prefixes are listed in
 * tests/fixtures/dug_curie_prefixes.json. Most common: MONDO → OBO PURL, EFO → EBI's
 * ontology browser, UMLS → NLM's UTS. GTOPDB → Guide to Pharmacology (its ligands;
 * bioregistry knows it under another name). Any other prefix → bioregistry.io,
 * which redirects to the prefix's own provider. */
export function conceptLink(id: string): KgLink | null {
  const i = id.indexOf(':')
  if (i < 1) return null
  const [prefix, local] = [id.slice(0, i), id.slice(i + 1)]
  const label = LOGIN.has(prefix) ? `${id} (login)` : id
  const local_ = encodeURIComponent(local)
  if (prefix === 'MONDO') return { label, url: `https://purl.obolibrary.org/obo/MONDO_${local_}` }
  if (prefix === 'EFO') return { label, url: `https://www.ebi.ac.uk/ols4/ontologies/efo/classes?obo_id=${encodeURIComponent(id)}` }
  if (prefix === 'UMLS') return { label, url: `https://uts.nlm.nih.gov/uts/umls/concept/${local_}` }
  if (prefix === 'GTOPDB') return { label, url: `https://www.guidetopharmacology.org/GRAC/LigandDisplayForward?ligandId=${local_}` }
  return { label, url: `https://bioregistry.io/${encodeURIComponent(id)}` }
}

// phv00001546.v1.p15 → variable number 1546, participant set p15
const VARIABLE = /^phv0*(\d+)\.v\d+\.(p\d+)$/

/**
 * Pages for a node of `graph` (the graph as drawn, collapsed or not). A concept:
 * its ontology page (conceptLink). Studies and variables: dbGaP, one link per
 * release: a study's study page; a variable's variable page, which also needs the
 * study release, picked by matching participant set (".p15").
 */
export function nodeLinks(node: KgNode, graph: KgGraph): KgLink[] {
  if (node.type === 'concept') {
    const link = conceptLink(node.id)
    return link ? [link] : []
  }
  const ids = node.versions ?? [node.id]
  if (node.type === 'study') {
    return ids.map((id) => ({ label: id, url: `${DBGAP}/study.cgi?study_id=${encodeURIComponent(id)}` }))
  }
  const studyIds = new Set(graph.edges.filter((e) => e.source === node.id).map((e) => e.target))
  const study = graph.nodes.find((n) => n.type === 'study' && studyIds.has(n.id))
  if (!study) return []
  const releases = study.versions ?? [study.id]
  return ids.flatMap((id) => {
    const m = VARIABLE.exec(id)
    if (!m) return []
    const release = releases.find((r) => r.endsWith(`.${m[2]}`)) ?? releases[0]
    return [{ label: id, url: `${DBGAP}/variable.cgi?study_id=${encodeURIComponent(release)}&phv=${m[1]}` }]
  })
}
