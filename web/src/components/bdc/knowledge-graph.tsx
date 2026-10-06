import { makeAssistantDataUI } from '@assistant-ui/react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { collapseVersions } from '@/kg/collapse'
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

function NodeDetails({ node }: { node: KgNode | null }) {
  if (!node) return <p className="text-muted-foreground">Click a node for details.</p>
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
      <dt className="text-muted-foreground">{TYPE_LABELS[node.type]}</dt>
      <dd className="font-medium">{node.label}</dd>
      <dt className="text-muted-foreground">ID</dt>
      <dd className="font-mono">{node.id}</dd>
      {node.versions && node.versions.length > 1 && (
        <>
          <dt className="text-muted-foreground">Releases</dt>
          <dd className="font-mono">{node.versions.join(', ')}</dd>
        </>
      )}
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
      <div ref={container} className="h-72 border-t" aria-label="Knowledge graph" role="img" />
      <div className="border-t px-3 py-2">
        <NodeDetails node={selected} />
      </div>
    </details>
  )
}

export const KnowledgeGraphUI = makeAssistantDataUI<KgGraph>({
  name: GRAPH_PART,
  render: ({ data }) => <KnowledgeGraph graph={data} />,
})
