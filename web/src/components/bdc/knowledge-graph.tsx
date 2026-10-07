import { ChevronRightIcon, ExternalLinkIcon, Maximize2Icon, MinusIcon, PlusIcon, ScanIcon } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { TooltipIconButton } from '@/components/assistant-ui/elements/tooltip-icon-button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { bridges, sharedOnly as onlyShared } from '@/kg/bridges'
import { collapseVersions } from '@/kg/collapse'
import { connections } from '@/kg/connections'
import { nodeLinks, type KgLink } from '@/kg/links'
import { studyList } from '@/kg/list'
import { mountFlow, type FlowView } from '@/kg/flow'
import { KG_LAYOUTS, mountGraph, type GraphView, type KgLayout } from '@/kg/mount'
import type { KgGraph, KgNode } from '@/kg/types'

// The demo's wrapper around src/kg (loaded on demand: see GraphUI in message-parts.tsx):
// a collapsible panel under the answer with the graph, or the same as a list, and
// the selected node's details, plus a button to show it all in a large dialog.
// Another host would write its own wrapper around mountGraph and studyList;
// nothing in src/kg depends on this file.

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
  { type: 'variable', shape: 'h-2.5 w-5 rounded-full' }, // the colour ramp
  { type: 'study', shape: 'size-2.5 rounded-[2px]' },
]

function Legend() {
  return (
    <ul aria-label="Legend" className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {LEGEND.map(({ type, shape }) => (
        <li key={type} className="flex items-center gap-1.5">
          <span
            className={shape}
            style={{
              background:
                type === 'variable'
                  ? 'linear-gradient(to right, var(--kg-variable-low), var(--kg-variable-high))'
                  : `var(--kg-${type})`,
            }}
          />
          {TYPE_LABELS[type]}
          {type === 'variable' && <span className="opacity-70">(darker: links more concepts)</span>}
        </li>
      ))}
    </ul>
  )
}

type ViewMode = 'graph' | 'flow' | 'list'
type ViewChoice = KgLayout | 'flow' | 'list'

const VIEW_LABELS: Record<ViewChoice, string> = {
  radial: 'Radial',
  columns: 'Columns',
  force: 'Force',
  flow: 'Flow',
  list: 'List',
}

/** One dropdown for the graph's layouts, the flow (Sankey) and the list: a layout is a
 * mountGraph option, flow is mountFlow, the list is this wrapper's own, but to the
 * user they're all ways to view it. */
function ViewPicker({ layout, mode, onLayout, onMode }: Pick<GraphBodyProps, 'layout' | 'mode' | 'onLayout' | 'onMode'>) {
  return (
    <label className="flex items-center gap-1.5">
      View
      <select
        value={mode === 'graph' ? layout : mode}
        onChange={(e) => {
          const v = e.target.value as ViewChoice
          if (v === 'flow' || v === 'list') onMode(v)
          else {
            onMode('graph')
            onLayout(v)
          }
        }}
        className="bg-background text-foreground rounded-md border px-1 py-0.5"
      >
        {[...KG_LAYOUTS, 'flow' as const, 'list' as const].map((v) => (
          <option key={v} value={v}>
            {VIEW_LABELS[v]}
          </option>
        ))}
      </select>
    </label>
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

function ConceptTags({ concepts }: { concepts: KgNode[] }) {
  return (
    <span className="flex shrink-0 gap-1">
      {concepts.map((c) => (
        <span key={c.id} className="bg-muted text-muted-foreground rounded px-1">
          {c.label}
        </span>
      ))}
    </span>
  )
}

// selected: shaded; outside the selection's connections: dimmed, as in the graph
const rowClass = (selected: boolean, dimmed: boolean) =>
  `flex min-w-0 flex-1 items-baseline gap-2 rounded-md px-1.5 py-1 text-start transition-opacity ${selected ? 'bg-muted' : 'hover:bg-muted/60'} ${dimmed ? 'opacity-35' : ''}`

/** The graph as text: studies (foldable) with their variables (studyList). Rows
 * select like nodes in the graph (clicking the selected one again clears it), and
 * dim like them too when not connected to the selection. Concept tags only when
 * there are several. */
function StudyList({ graph, selected, onSelect }: { graph: KgGraph; selected: KgNode | null; onSelect: (n: KgNode) => void }) {
  const groups = useMemo(() => studyList(graph), [graph])
  const linked = useMemo(() => (selected ? connections(graph, selected.id).nodes : null), [graph, selected])
  const dimmed = (id: string) => linked !== null && !linked.has(id)
  const several = graph.nodes.filter((n) => n.type === 'concept').length > 1
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set())
  const toggle = (id: string) =>
    setFolded((f) => {
      const next = new Set(f)
      if (!next.delete(id)) next.add(id)
      return next
    })
  return (
    <ul aria-label="Studies and their variables" className="divide-y">
      {groups.map(({ study, concepts, variables }) => {
        const key = study?.id ?? '(no study)'
        const open = !folded.has(key)
        return (
          <li key={key} className="px-1.5 py-1">
            <div className="flex items-center gap-0.5">
              <button
                type="button"
                aria-expanded={open}
                aria-label={`${open ? 'Fold' : 'Unfold'} ${study?.label ?? 'variables without a study'}`}
                onClick={() => toggle(key)}
                className="text-muted-foreground hover:text-foreground rounded p-1"
              >
                <ChevronRightIcon className={`size-3.5 transition-transform ${open ? 'rotate-90' : ''}`} />
              </button>
              {study ? (
                <button
                  type="button"
                  onClick={() => onSelect(study)}
                  className={rowClass(selected?.id === study.id, dimmed(study.id))}
                >
                  <span className="size-2 shrink-0 self-center rounded-[2px]" style={{ background: 'var(--kg-study)' }} />
                  <span className="truncate font-medium">{study.label}</span>
                  <span className="text-muted-foreground shrink-0 font-mono">{study.id}</span>
                  <span className="text-muted-foreground shrink-0">· {variables.length}</span>
                  {several && <ConceptTags concepts={concepts} />}
                </button>
              ) : (
                <span className="text-muted-foreground px-1.5 py-1">Variables without a study</span>
              )}
            </div>
            {open && (
              <ul className="pl-6">
                {variables.map(({ variable, concepts: vc, weight }) => (
                  <li key={variable.id} className="flex">
                    <button
                      type="button"
                      onClick={() => onSelect(variable)}
                      className={rowClass(selected?.id === variable.id, dimmed(variable.id))}
                    >
                      <span
                        className="size-2 shrink-0 self-center rounded-full"
                        style={{
                          background: `color-mix(in srgb, var(--kg-variable-high) ${Math.round(weight * 100)}%, var(--kg-variable-low))`,
                        }}
                      />
                      <span className="font-mono">{variable.label}</span>
                      <span className="text-muted-foreground truncate font-mono">{variable.id}</span>
                      {several && <ConceptTags concepts={vc} />}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** Legend, graph and the clicked node's details: one mountGraph view. Rendered
 * inline and, separately, in the maximized dialog. Carries the --kg-* colours
 * (index.css), so they apply in the dialog's portal too. */
type GraphBodyProps = {
  json: string
  shown: KgGraph
  canvasClass: string
  layout: KgLayout
  onLayout: (l: KgLayout) => void
  mode: ViewMode
  onMode: (m: ViewMode) => void
  sharedOnly: boolean
  onSharedOnly: (on: boolean) => void
  canShare: boolean // some study connects two or more concepts: offer the filter
  zoomGestures: boolean | 'modifier' // see mountGraph: 'modifier' where the chat scrolls around the graph
}

const ZOOM_STEP = 1.3
const ZOOM_KEY = /Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘' : 'Ctrl'

function GraphBody(props: GraphBodyProps) {
  const { json, shown, canvasClass, layout, onLayout, mode, onMode, sharedOnly, onSharedOnly, canShare, zoomGestures } = props
  const container = useRef<HTMLDivElement>(null)
  const view = useRef<GraphView | null>(null)
  const flowContainer = useRef<HTMLDivElement>(null)
  const flowView = useRef<FlowView | null>(null)
  const [selected, setSelected] = useState<KgNode | null>(null)
  const [zoomHint, setZoomHint] = useState(false)
  const hintTimer = useRef<number | undefined>(undefined)
  const showZoomHint = () => {
    setZoomHint(true)
    clearTimeout(hintTimer.current)
    hintTimer.current = window.setTimeout(() => setZoomHint(false), 1500)
  }

  // mounts with the layout of the moment; later changes go through setOptions below
  useEffect(() => {
    const v = mountGraph(container.current!, JSON.parse(json) as KgGraph, {
      layout,
      sharedOnly,
      zoomGestures,
      onZoomHint: showZoomHint,
      onSelect: (n) => {
        setSelected(n)
        flowView.current?.select(n?.id ?? null) // the other view follows
      },
    })
    view.current = v
    // the container's size can change without the window's (details reopened,
    // dialog opening animation): keep the graph fitted to it
    const observer = new ResizeObserver(() => v.resize())
    observer.observe(container.current!)
    return () => {
      observer.disconnect()
      v.destroy()
      view.current = null
      setSelected(null)
      clearTimeout(hintTimer.current)
    }
  }, [json]) // eslint-disable-line react-hooks/exhaustive-deps -- layout: see below

  // the flow (Sankey): its own view of the same graph, kept mounted like the graph
  useEffect(() => {
    const v = mountFlow(flowContainer.current!, JSON.parse(json) as KgGraph, {
      sharedOnly,
      onSelect: (n) => {
        setSelected(n)
        view.current?.select(n?.id ?? null)
      },
    })
    flowView.current = v
    const observer = new ResizeObserver(() => v.resize()) // laid out for its size
    observer.observe(flowContainer.current!)
    return () => {
      observer.disconnect()
      v.destroy()
      flowView.current = null
    }
  }, [json]) // eslint-disable-line react-hooks/exhaustive-deps -- sharedOnly: see below

  useEffect(() => view.current?.setOptions({ layout }), [layout])
  useEffect(() => {
    view.current?.setOptions({ sharedOnly })
    flowView.current?.setOptions({ sharedOnly })
  }, [sharedOnly])

  // picked in the list: both views show it selected too, for when they're switched to
  // (clicking the selected row again clears it, like the graph's background)
  const selectFromList = (node: KgNode) => {
    const next = selected?.id === node.id ? null : node
    setSelected(next)
    view.current?.select(next?.id ?? null)
    flowView.current?.select(next?.id ?? null)
  }

  return (
    <div data-slot="bdc-graph" className="flex min-h-0 flex-1 flex-col text-xs">
      <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 border-t px-3 py-1.5">
        <Legend />
        <div className="ml-auto flex items-center gap-3">
          {canShare && (
            <Tooltip>
              <TooltipTrigger asChild>
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={sharedOnly} onChange={(e) => onSharedOnly(e.target.checked)} />
                  Shared only
                </label>
              </TooltipTrigger>
              <TooltipContent side="bottom">Only the studies with variables on two or more concepts</TooltipContent>
            </Tooltip>
          )}
          <ViewPicker layout={layout} mode={mode} onLayout={onLayout} onMode={onMode} />
        </div>
      </div>
      {mode === 'list' && (
        <div className={`overflow-y-auto border-t ${canvasClass}`}>
          <StudyList graph={shown} selected={selected} onSelect={selectFromList} />
        </div>
      )}
      <div
        ref={flowContainer}
        className={`border-t ${canvasClass} ${mode === 'flow' ? '' : 'hidden'}`}
        aria-label="Knowledge graph as flows from concepts to studies. The List view shows the same as text."
      />
      {/* kept mounted while another view shows, so the graph keeps its layout and zoom */}
      <div className={`relative border-t ${canvasClass} ${mode === 'graph' ? '' : 'hidden'}`}>
        {/* sized by height, not `absolute inset-0`: Cytoscape gives its container
            `position: relative` from an unlayered style sheet, which beats Tailwind's
            layered utilities, so `absolute` would be dropped and the box collapse */}
        <div
          ref={container}
          className="h-full w-full"
          role="img"
          aria-label="Knowledge graph. The List view shows the same studies and variables as text."
        />
        <p
          aria-hidden
          className={`bg-foreground/75 text-background pointer-events-none absolute inset-x-0 top-1/2 mx-auto w-fit -translate-y-1/2 rounded-md px-3 py-1.5 transition-opacity ${zoomHint ? 'opacity-100' : 'opacity-0'}`}
        >
          Hold {ZOOM_KEY} and scroll to zoom
        </p>
        <div className="bg-background/80 absolute right-1.5 bottom-1.5 flex rounded-md border">
          <TooltipIconButton tooltip="Zoom in" side="top" onClick={() => view.current?.zoomBy(ZOOM_STEP)}>
            <PlusIcon />
          </TooltipIconButton>
          <TooltipIconButton tooltip="Zoom out" side="top" onClick={() => view.current?.zoomBy(1 / ZOOM_STEP)}>
            <MinusIcon />
          </TooltipIconButton>
          <TooltipIconButton tooltip="Show all" side="top" onClick={() => view.current?.fit()}>
            <ScanIcon />
          </TooltipIconButton>
        </div>
      </div>
      <div className="border-t px-3 py-2">
        <NodeDetails node={selected} graph={shown} />
      </div>
    </div>
  )
}

export function KnowledgeGraph({ graph }: { graph: KgGraph }) {
  const [maximized, setMaximized] = useState(false)
  // shared by the panel and the dialog
  const [layout, setLayout] = useState<KgLayout>('radial')
  const [mode, setMode] = useState<ViewMode>('graph')
  const [sharedOnly, setSharedOnly] = useState(false)
  // the message is re-rendered on every stream event, and done delivers the same
  // graph again as a new object: redraw only when the content changes
  const json = JSON.stringify(graph)
  const merged = useMemo(() => collapseVersions(JSON.parse(json) as KgGraph), [json])
  const sharedStudies = useMemo(() => {
    const ids = bridges(merged)
    return merged.nodes.filter((n) => n.type === 'study' && ids.has(n.id)).length
  }, [merged])
  // what the list and the details see: the same as mountGraph draws
  const shown = useMemo(() => (sharedOnly ? onlyShared(merged) : merged), [merged, sharedOnly])
  const title = (
    <>
      <span className="text-foreground font-medium">Knowledge graph</span> · {summary(merged)}
      {sharedStudies > 0 && ` · ${sharedStudies} shared`}
    </>
  )
  const filter = { sharedOnly, onSharedOnly: setSharedOnly, canShare: sharedStudies > 0 }

  return (
    <>
      <details open className="my-3 rounded-lg border text-xs">
        <summary className="text-muted-foreground flex cursor-pointer items-center gap-2 px-3 py-1.5 select-none">
          <span className="flex-1">{title}</span>
          <TooltipIconButton
            tooltip="Maximize"
            side="left"
            className="size-7 p-1.5"
            onClick={(e) => {
              e.preventDefault() // a click in <summary> would also fold the panel
              setMaximized(true)
            }}
          >
            <Maximize2Icon />
          </TooltipIconButton>
        </summary>
        <GraphBody
          json={json}
          shown={shown}
          canvasClass="h-72"
          layout={layout}
          onLayout={setLayout}
          mode={mode}
          onMode={setMode}
          {...filter}
          zoomGestures="modifier"
        />
      </details>
      <Dialog open={maximized} onOpenChange={setMaximized}>
        <DialogContent
          aria-describedby={undefined}
          className="flex h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-none flex-col gap-0 p-0 sm:max-w-none"
        >
          <DialogTitle className="text-muted-foreground px-3 py-2.5 pr-12 text-xs font-normal">{title}</DialogTitle>
          {maximized && (
            <GraphBody
              json={json}
              shown={shown}
              canvasClass="min-h-0 flex-1"
              layout={layout}
              onLayout={setLayout}
              mode={mode}
              onMode={setMode}
              {...filter}
              zoomGestures
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
