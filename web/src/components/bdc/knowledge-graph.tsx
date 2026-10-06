import { makeAssistantDataUI } from '@assistant-ui/react'
import { ExternalLinkIcon, Maximize2Icon } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { collapseVersions } from '@/kg/collapse'
import { nodeLinks, type KgLink } from '@/kg/links'
import { mountGraph } from '@/kg/mount'
import type { KgGraph, KgNode } from '@/kg/types'
import { GRAPH_PART } from '@/lib/bdc-adapter'

// The demo's wrapper around src/kg: a collapsible panel under the answer with the
// graph and the clicked node's details, and a button to show it all in a large
// dialog. Another host would write its own wrapper
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

/** Legend, graph and the clicked node's details: one mountGraph view. Rendered
 * inline and, separately, in the maximized dialog. Carries the --kg-* colours
 * (index.css), so they apply in the dialog's portal too. */
function GraphBody({ json, shown, canvasClass }: { json: string; shown: KgGraph; canvasClass: string }) {
  const container = useRef<HTMLDivElement>(null)
  const [selected, setSelected] = useState<KgNode | null>(null)

  useEffect(() => {
    const view = mountGraph(container.current!, JSON.parse(json) as KgGraph, { onSelect: setSelected })
    // the container's size can change without the window's (details reopened,
    // dialog opening animation): keep the graph fitted to it
    const observer = new ResizeObserver(() => view.resize())
    observer.observe(container.current!)
    return () => {
      observer.disconnect()
      view.destroy()
      setSelected(null)
    }
  }, [json])

  return (
    <div data-slot="bdc-graph" className="flex min-h-0 flex-1 flex-col text-xs">
      <Legend />
      <div ref={container} className={`border-t ${canvasClass}`} aria-label="Knowledge graph" role="img" />
      <div className="border-t px-3 py-2">
        <NodeDetails node={selected} graph={shown} />
      </div>
    </div>
  )
}

export function KnowledgeGraph({ graph }: { graph: KgGraph }) {
  const [maximized, setMaximized] = useState(false)
  // the message is re-rendered on every stream event, and done delivers the same
  // graph again as a new object: redraw only when the content changes
  const json = JSON.stringify(graph)
  const shown = useMemo(() => collapseVersions(JSON.parse(json) as KgGraph), [json])
  const title = (
    <>
      <span className="text-foreground font-medium">Knowledge graph</span> · {summary(shown)}
    </>
  )

  return (
    <>
      <details open className="my-3 rounded-lg border text-xs">
        <summary className="text-muted-foreground flex cursor-pointer items-center gap-2 px-3 py-1.5 select-none">
          <span className="flex-1">{title}</span>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Maximize knowledge graph"
            title="Maximize"
            onClick={(e) => {
              e.preventDefault() // a click in <summary> would also fold the panel
              setMaximized(true)
            }}
          >
            <Maximize2Icon />
          </Button>
        </summary>
        <GraphBody json={json} shown={shown} canvasClass="h-72" />
      </details>
      <Dialog open={maximized} onOpenChange={setMaximized}>
        <DialogContent
          aria-describedby={undefined}
          className="flex h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-none flex-col gap-0 p-0 sm:max-w-none"
        >
          <DialogTitle className="text-muted-foreground px-3 py-2.5 pr-12 text-xs font-normal">{title}</DialogTitle>
          {maximized && <GraphBody json={json} shown={shown} canvasClass="min-h-0 flex-1" />}
        </DialogContent>
      </Dialog>
    </>
  )
}

export const KnowledgeGraphUI = makeAssistantDataUI<KgGraph>({
  name: GRAPH_PART,
  render: ({ data }) => <KnowledgeGraph graph={data} />,
})
