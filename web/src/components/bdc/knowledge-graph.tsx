import { makeAssistantDataUI } from '@assistant-ui/react'
import { ExternalLinkIcon } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { collapseVersions } from '@/kg/collapse'
import { nodeLinks, type KgLink } from '@/kg/links'
import { mountGraph, type GraphView } from '@/kg/mount'
import type { KgGraph, KgNode } from '@/kg/types'
import { GRAPH_PART } from '@/lib/bdc-adapter'

// The demo's wrapper around src/kg: a collapsible panel under the answer with the
// graph and the clicked node's details. Another host would write its own wrapper
// around mountGraph; nothing in src/kg depends on this file.

const TYPE_LABELS: Record<KgNode['type'], string> = { concept: 'Concept', variable: 'Variable', study: 'Study' }

const counted = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

// counts what mountGraph draws: versions collapsed (its default)
function summary(g: KgGraph) {
  const count = (t: KgNode['type']) => g.nodes.filter((n) => n.type === t).length
  return [
    counted(count('concept'), 'concept', 'concepts'),
    counted(count('variable'), 'variable', 'variables'),
    counted(count('study'), 'study', 'studies'),
  ].join(' · ')
}

// Swatches drawn like the nodes: same --kg-* colours (index.css), same shapes
const LEGEND: { type: KgNode['type']; shape: string }[] = [
  { type: 'concept', shape: 'size-3 rounded-full' },
  { type: 'variable', shape: 'size-2.5 rounded-full' },
  { type: 'study', shape: 'size-2.5 rounded-[2px]' },
]

function Legend() {
  return (
    <ul aria-label="Legend" className="text-muted-foreground flex items-center gap-3 border-t px-3 py-1.5">
      {LEGEND.map(({ type, shape }) => (
        <li key={type} className="flex items-center gap-1.5">
          <span className={shape} style={{ background: `var(--kg-${type})` }} />
          {TYPE_LABELS[type]}
        </li>
      ))}
    </ul>
  )
}

function ExternalLink({ link }: { link: KgLink }) {
  return (
    <a
      href={link.url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 font-mono underline-offset-2 hover:underline"
    >
      {link.label}
      <ExternalLinkIcon className="size-3" />
    </a>
  )
}

// The ID is shown as its link(s): a concept's ontology page, or one dbGaP page per
// release of a study or variable (merged releases list each versioned ID).
function NodeDetails({ node, graph }: { node: KgNode | null; graph: KgGraph }) {
  if (!node) return <p className="text-muted-foreground">Click a node for details.</p>
  const links = nodeLinks(node, graph)
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
      <dt className="text-muted-foreground">{TYPE_LABELS[node.type]}</dt>
      <dd className="font-medium">{node.label}</dd>
      <dt className="text-muted-foreground">{links.length > 1 ? 'IDs' : 'ID'}</dt>
      <dd className="flex flex-wrap gap-x-3 font-mono">
        {links.length ? links.map((l) => <ExternalLink key={l.url} link={l} />) : node.id}
      </dd>
      {node.related_concepts_count !== undefined && (
        <>
          <dt className="text-muted-foreground">Other concepts</dt>
          <dd>{node.related_concepts_count}</dd>
        </>
      )}
    </dl>
  )
}

export function KnowledgeGraph({ graph }: { graph: KgGraph }) {
  const container = useRef<HTMLDivElement>(null)
  const view = useRef<GraphView | null>(null)
  const [selected, setSelected] = useState<KgNode | null>(null)
  // the message is re-rendered on every stream event, and done delivers the same
  // graph again as a new object: redraw only when the content changes
  const json = JSON.stringify(graph)
  const shown = useMemo(() => collapseVersions(JSON.parse(json) as KgGraph), [json])

  useEffect(() => {
    const v = mountGraph(container.current!, JSON.parse(json) as KgGraph, { onSelect: setSelected })
    view.current = v
    return () => {
      v.destroy()
      view.current = null
      setSelected(null)
    }
  }, [json])

  return (
    <details
      open
      data-slot="bdc-graph"
      className="my-3 rounded-lg border text-xs"
      onToggle={(e) => e.currentTarget.open && view.current?.resize()}
    >
      <summary className="text-muted-foreground cursor-pointer px-3 py-2 select-none">
        <span className="text-foreground font-medium">Knowledge graph</span> · {summary(shown)}
      </summary>
      <Legend />
      <div ref={container} className="h-72 border-t" aria-label="Knowledge graph" role="img" />
      <div className="border-t px-3 py-2">
        <NodeDetails node={selected} graph={shown} />
      </div>
    </details>
  )
}

export const KnowledgeGraphUI = makeAssistantDataUI<KgGraph>({
  name: GRAPH_PART,
  render: ({ data }) => <KnowledgeGraph graph={data} />,
})
