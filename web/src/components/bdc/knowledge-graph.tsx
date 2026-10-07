import { ChevronRightIcon, ExternalLinkIcon, Maximize2Icon, MinusIcon, PlusIcon, ScanIcon } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { TooltipIconButton } from '@/components/assistant-ui/elements/tooltip-icon-button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { bridges, sharedOnly as onlyShared } from '@/kg/bridges'
import { collapseVersions } from '@/kg/collapse'
import { nodeLinks, type KgLink } from '@/kg/links'
import { studyList } from '@/kg/list'
import { mountFlow, type FlowView } from '@/kg/flow'
import { focusConnections, isPair, pairVariables, sameFocus, type KgFocus } from '@/kg/focus'
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
function NodeDetails({ node, graph }: { node: KgNode; graph: KgGraph }) {
  const all = nodeLinks(node, graph)
  const links = all.filter((l) => !l.note) // the node's own pages
  const standIns = all.filter((l) => l.note) // e.g. a variable without a dbGaP accession: its study
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
      <dt className="text-muted-foreground">{TYPE_LABELS[node.type]}</dt>
      <dd className="font-medium">{node.label}</dd>
      <dt className="text-muted-foreground">{links.length > 1 ? 'IDs' : 'ID'}</dt>
      <dd className="flex flex-wrap gap-x-3 font-mono">
        {links.length ? links.map((l) => <ExternalLink key={l.url} link={l} />) : node.id}
      </dd>
      {standIns.length > 0 && (
        <>
          <dt className="text-muted-foreground">dbGaP</dt>
          <dd className="flex flex-wrap items-baseline gap-x-3">
            <span className="text-muted-foreground">{standIns[0].note} ·</span>
            {standIns.map((l) => (
              <ExternalLink key={l.url} link={{ ...l, label: `study ${l.label}` }} />
            ))}
          </dd>
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

/** A concept × study pair: both, and the study's variables on the concept, each
 * linking to its dbGaP page. */
function PairDetails({ concept, study, graph }: { concept: KgNode; study: KgNode; graph: KgGraph }) {
  const variables = pairVariables(graph, concept.id, study.id)
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
      <dt className="text-muted-foreground">Concept</dt>
      <dd className="font-medium">{concept.label}</dd>
      <dt className="text-muted-foreground">Study</dt>
      <dd className="font-medium">{study.label}</dd>
      <dt className="text-muted-foreground">Variables</dt>
      <dd className="flex max-h-24 flex-wrap gap-x-3 overflow-y-auto font-mono">
        {variables.map((v) => {
          // only a variable's own page; one without a dbGaP accession stays plain text
          const link = nodeLinks(v, graph).find((l) => !l.note)
          return link ? <ExternalLink key={v.id} link={{ ...link, label: v.label }} /> : <span key={v.id}>{v.label}</span>
        })}
      </dd>
    </dl>
  )
}

function FocusDetails({ focus, graph }: { focus: KgFocus | null; graph: KgGraph }) {
  const byId = (id: string) => graph.nodes.find((n) => n.id === id)
  if (focus && isPair(focus)) {
    const [concept, study] = [byId(focus.concept), byId(focus.study)]
    if (concept && study) return <PairDetails concept={concept} study={study} graph={graph} />
  }
  const node = focus && !isPair(focus) ? byId(focus.node) : undefined
  if (node) return <NodeDetails node={node} graph={graph} />
  return <p className="text-muted-foreground">Click a node for details.</p>
}

// selected: shaded; outside the selection's connections: dimmed, as in the graph
const rowClass = (selected: boolean, dimmed: boolean) =>
  `flex min-w-0 flex-1 items-baseline gap-2 rounded-md px-1.5 py-1 text-start transition-opacity ${selected ? 'bg-muted' : 'hover:bg-muted/60'} ${dimmed ? 'opacity-35' : ''}`

/** The graph as text: studies (foldable), each with a row per concept (that
 * concept × study pair) and the variables under it (studyList). Study and variable
 * rows focus like nodes in the graph, concept rows focus their pair; picking the
 * focused row again clears it. Rows dim like the graph when not connected to the focus. */
function StudyList({ graph, focus, onPick }: { graph: KgGraph; focus: KgFocus | null; onPick: (f: KgFocus) => void }) {
  const groups = useMemo(() => studyList(graph), [graph])
  const linked = useMemo(() => (focus ? focusConnections(graph, focus).nodes : null), [graph, focus])
  const dimmed = (id: string) => linked !== null && !linked.has(id)
  const isNode = (id: string) => !!focus && !isPair(focus) && focus.node === id
  const isPairOf = (concept: string, study: string) =>
    !!focus && isPair(focus) && focus.concept === concept && focus.study === study
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set())
  const toggle = (id: string) =>
    setFolded((f) => {
      const next = new Set(f)
      if (!next.delete(id)) next.add(id)
      return next
    })
  return (
    <ul aria-label="Studies, their concepts and variables" className="divide-y">
      {groups.map(({ study, variables, byConcept }) => {
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
                  onClick={() => onPick({ node: study.id })}
                  className={rowClass(isNode(study.id), dimmed(study.id))}
                >
                  <span className="size-2 shrink-0 self-center rounded-[2px]" style={{ background: 'var(--kg-study)' }} />
                  <span className="truncate font-medium">{study.label}</span>
                  <span className="text-muted-foreground shrink-0 font-mono">{study.id}</span>
                  <span className="text-muted-foreground shrink-0">· {variables.length}</span>
                </button>
              ) : (
                <span className="text-muted-foreground px-1.5 py-1">Variables without a study</span>
              )}
            </div>
            {open && (
              <ul className="pl-6">
                {byConcept.map(({ concept, variables: vs }) => (
                  <li key={concept?.id ?? '(no concept)'}>
                    <div className="flex">
                      {concept && study ? (
                        <button
                          type="button"
                          title={`What this study has on ${concept.label}`}
                          onClick={() => onPick({ concept: concept.id, study: study.id })}
                          // a pair row: lit only when both its concept and its study are
                          className={rowClass(isPairOf(concept.id, study.id), dimmed(concept.id) || dimmed(study.id))}
                        >
                          <span className="size-2 shrink-0 self-center rounded-full" style={{ background: 'var(--kg-concept)' }} />
                          <span>{concept.label}</span>
                          <span className="text-muted-foreground shrink-0">· {vs.length}</span>
                        </button>
                      ) : (
                        <span className="text-muted-foreground px-1.5 py-1">{concept?.label ?? 'No concept'}</span>
                      )}
                    </div>
                    <ul className="pl-6">
                      {vs.map(({ variable, weight }) => (
                        <li key={variable.id} className="flex">
                          <button
                            type="button"
                            onClick={() => onPick({ node: variable.id })}
                            className={rowClass(isNode(variable.id), dimmed(variable.id))}
                          >
                            <span
                              className="size-2 shrink-0 self-center rounded-full"
                              style={{
                                background: `color-mix(in srgb, var(--kg-variable-high) ${Math.round(weight * 100)}%, var(--kg-variable-low))`,
                              }}
                            />
                            <span className="font-mono">{variable.label}</span>
                            <span className="text-muted-foreground truncate font-mono">{variable.id}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
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
  // what's picked, the same in every view: a node or a concept × study pair
  const [focus, setFocus] = useState<KgFocus | null>(null)
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
      onFocus: (f) => {
        setFocus(f)
        flowView.current?.focus(f) // the other view follows
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
      setFocus(null)
      clearTimeout(hintTimer.current)
    }
  }, [json]) // eslint-disable-line react-hooks/exhaustive-deps -- layout: see below

  // the flow (Sankey): its own view of the same graph, kept mounted like the graph
  useEffect(() => {
    const v = mountFlow(flowContainer.current!, JSON.parse(json) as KgGraph, {
      sharedOnly,
      onFocus: (f) => {
        setFocus(f)
        view.current?.focus(f)
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

  // picked in the list: both views show it too, for when they're switched to
  // (picking the focused row or tag again clears it, like the graph's background)
  const pickFromList = (f: KgFocus) => {
    const next = sameFocus(focus, f) ? null : f
    setFocus(next)
    view.current?.focus(next)
    flowView.current?.focus(next)
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
          <StudyList graph={shown} focus={focus} onPick={pickFromList} />
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
        <FocusDetails focus={focus} graph={shown} />
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
