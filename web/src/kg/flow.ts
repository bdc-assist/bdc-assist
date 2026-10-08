import { sankey, sankeyLinkHorizontal, type SankeyLink, type SankeyNode } from 'd3-sankey'

import { sharedOnly } from './bridges'
import { collapseVersions } from './collapse'
import { focusConnections, isPair, type KgFocus } from './focus'
import type { KgGraph, KgNode } from './types'
import type { KgView, KgViewOptions } from './view'

export type FlowNode = { id: string; node: KgNode } // a concept or a study
export type FlowLink = { source: string; target: string; variables: string[] } // concept → study
export type FlowData = { nodes: FlowNode[]; links: FlowLink[] }

/**
 * The graph as flows from concepts to studies: one link per concept and study,
 * carrying the study's variables on that concept (its width). A variable on two
 * concepts counts in both. Only studies with variables appear; concepts and links
 * keep graph order.
 */
export function flowData(graph: KgGraph): FlowData {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]))
  const studyOf = new Map<string, string>()
  const conceptsOf = new Map<string, string[]>()
  for (const e of graph.edges) {
    const t = byId.get(e.target)?.type
    if (t === 'study') studyOf.set(e.source, e.target)
    if (t === 'concept') conceptsOf.set(e.source, [...(conceptsOf.get(e.source) ?? []), e.target])
  }
  const links = new Map<string, FlowLink>()
  for (const n of graph.nodes) {
    const study = n.type === 'variable' ? studyOf.get(n.id) : undefined
    if (!study) continue
    for (const concept of conceptsOf.get(n.id) ?? []) {
      const key = JSON.stringify([concept, study])
      const link = links.get(key) ?? { source: concept, target: study, variables: [] }
      link.variables.push(n.id)
      links.set(key, link)
    }
  }
  const used = new Set([...links.values()].flatMap((l) => [l.source, l.target]))
  const nodes = graph.nodes.filter((n) => used.has(n.id)).map((n) => ({ id: n.id, node: n }))
  return { nodes, links: [...links.values()] }
}

export type FlowOptions = KgViewOptions
export type FlowView = KgView<FlowOptions>

type SNode = SankeyNode<FlowNode, FlowLink & { value: number }>
type SLink = SankeyLink<FlowNode, FlowLink & { value: number }>

const SVG = 'http://www.w3.org/2000/svg'
const NODE_WIDTH = 10
const FADED = '0.15'

// colours: the same --kg-* custom properties as mountGraph; SVG takes them as-is.
// Bands are bundles of variables, so they're blue: the middle of the variable ramp.
const fill = {
  concept: 'var(--kg-concept, #d97706)',
  study: 'var(--kg-study, #059669)',
  band: 'color-mix(in srgb, var(--kg-variable-low, #93c5fd), var(--kg-variable-high, #1e40af))',
} as const

/**
 * Draw `graph` into `container` (which needs a size) as a Sankey diagram: concepts
 * on the left, studies on the right, each concept → study band as wide as the
 * study's variables on that concept (layout by d3-sankey, drawn as plain SVG).
 * Clicking a concept or study focuses it, a band its concept × study pair; what the
 * focus connects stays, the rest fades (see focusConnections); clicking the
 * background clears it. Same interface as mountGraph, so a host can switch between
 * them. No framework required.
 */
export function mountFlow(container: HTMLElement, graph: KgGraph, options: FlowOptions = {}): FlowView {
  let opts: FlowOptions = { collapseVersions: true, ...options }
  let current = graph
  let drawn: KgGraph = graph
  let data: FlowData = { nodes: [], links: [] }
  let focused: KgFocus | null = null

  const svg = document.createElementNS(SVG, 'svg')
  svg.setAttribute('role', 'img')
  svg.style.display = 'block'
  svg.style.font = 'inherit'
  container.append(svg)
  svg.addEventListener('click', (e) => {
    const hit = (e.target as Element).closest('[data-id], [data-link]')
    const link = hit?.getAttribute('data-link')
    const id = hit?.getAttribute('data-id')
    const [concept, study] = link?.split('->') ?? []
    const f: KgFocus | null = link ? { concept, study } : id ? { node: id } : null
    focus(f)
    opts.onFocus?.(f)
  })

  function prepare() {
    drawn = opts.collapseVersions ? collapseVersions(current) : current
    if (opts.sharedOnly) drawn = sharedOnly(drawn)
    data = flowData(drawn)
    focused = null
  }

  function render() {
    const width = container.clientWidth || 600
    const height = container.clientHeight || 300
    svg.setAttribute('width', String(width))
    svg.setAttribute('height', String(height))
    svg.replaceChildren()
    if (!data.links.length) return
    // room for labels: concepts' on the left, studies' (longer) on the right
    const left = Math.min(150, width * 0.22)
    const right = Math.min(260, width * 0.38)
    const layout = sankey<FlowNode, FlowLink & { value: number }>()
      .nodeId((n) => n.id)
      .nodeWidth(NODE_WIDTH)
      .nodePadding(Math.max(2, Math.min(10, height / (data.nodes.length * 3))))
      .extent([
        [left, 8],
        [width - right, height - 8],
      ])
    const { nodes, links } = layout({
      nodes: data.nodes.map((n) => ({ ...n })),
      links: data.links.map((l) => ({ ...l, value: l.variables.length })),
    })
    const path = sankeyLinkHorizontal()
    const g = (cls: string) => {
      const el = svg.appendChild(document.createElementNS(SVG, 'g'))
      el.setAttribute('class', cls)
      return el
    }
    const bands = g('kg-flow-links')
    for (const l of links as SLink[]) {
      const [s, t] = [l.source as SNode, l.target as SNode]
      const el = bands.appendChild(document.createElementNS(SVG, 'path'))
      el.setAttribute('d', path(l) ?? '')
      el.setAttribute('data-link', `${s.id}->${t.id}`)
      Object.assign(el.style, { fill: 'none', stroke: fill.band, strokeOpacity: '0.45', strokeWidth: String(Math.max(1, l.width ?? 1)), cursor: 'pointer' })
      title(el, `${s.node.label} → ${t.node.label}: ${l.value} variable${l.value === 1 ? '' : 's'}`)
    }
    const boxes = g('kg-flow-nodes')
    for (const n of nodes as SNode[]) {
      const [x0, y0, x1, y1] = [n.x0 ?? 0, n.y0 ?? 0, n.x1 ?? 0, n.y1 ?? 0]
      const isConcept = n.node.type === 'concept'
      const item = boxes.appendChild(document.createElementNS(SVG, 'g'))
      item.setAttribute('data-id', n.id)
      item.style.cursor = 'pointer'
      const rect = item.appendChild(document.createElementNS(SVG, 'rect'))
      for (const [k, v] of Object.entries({ x: x0, y: y0, width: x1 - x0, height: Math.max(1, y1 - y0), rx: 2 })) rect.setAttribute(k, String(v))
      if (n.node.term) {
        // a search word standing in for a concept: hollow, in the concept colour
        rect.dataset.term = ''
        Object.assign(rect.style, { fill: 'none', stroke: fill.concept, strokeWidth: '1.5' })
      } else rect.style.fill = isConcept ? fill.concept : fill.study
      const text = item.appendChild(document.createElementNS(SVG, 'text'))
      text.setAttribute('x', String(isConcept ? x0 - 6 : x1 + 6))
      text.setAttribute('y', String((y0 + y1) / 2))
      text.setAttribute('dominant-baseline', 'middle')
      text.setAttribute('text-anchor', isConcept ? 'end' : 'start')
      Object.assign(text.style, { fill: 'var(--kg-label, #475569)', fontSize: isConcept ? '12px' : '10px', fontWeight: isConcept ? '600' : '400' })
      text.textContent = clip(n.node.label, ((isConcept ? left : right) - 10) / (isConcept ? 6.5 : 5.5))
      title(item, `${n.node.label} (${n.value} variable${n.value === 1 ? '' : 's'})`)
    }
    highlight()
  }

  // like mountGraph: what the focus connects stays, the rest fades. A band stays when
  // both its ends do, which for a pair is just its own band.
  function highlight() {
    const linked = focused ? focusConnections(drawn, focused).nodes : null
    const outlined = focused && !isPair(focused) ? focused.node : null
    svg.querySelectorAll<SVGElement>('.kg-flow-nodes [data-id]').forEach((el) => {
      el.style.opacity = linked && !linked.has(el.dataset.id ?? '') ? FADED : ''
      const rect = el.querySelector('rect')
      if (!rect) return
      const term = rect.dataset.term !== undefined
      const on = el.dataset.id === outlined
      rect.style.stroke = on ? 'var(--kg-selected, #0f172a)' : term ? fill.concept : ''
      rect.style.strokeWidth = on ? '2' : term ? '1.5' : ''
    })
    svg.querySelectorAll<SVGElement>('[data-link]').forEach((el) => {
      const [s, t] = (el.dataset.link ?? '').split('->')
      el.style.opacity = linked && !(linked.has(s) && linked.has(t)) ? FADED : ''
    })
  }

  function focus(f: KgFocus | null) {
    focused = f
    highlight()
  }

  prepare()
  render()

  return {
    update(g, o) {
      if (o) opts = { ...opts, ...o }
      current = g
      opts.onFocus?.(null)
      prepare()
      render()
    },
    setOptions(o) {
      const changed = (k: 'collapseVersions' | 'sharedOnly') => o[k] !== undefined && o[k] !== opts[k]
      const redraw = changed('collapseVersions') || changed('sharedOnly')
      opts = { ...opts, ...o }
      if (!redraw) return
      opts.onFocus?.(null)
      prepare()
      render()
    },
    focus,
    resize: render, // laid out for the container's size: text stays sharp
    refreshStyle() {}, // colours are CSS variables, so they follow on their own
    destroy() {
      svg.remove()
    },
  }
}

function title(el: Element, text: string) {
  el.appendChild(document.createElementNS(SVG, 'title')).textContent = text
}

/** Cut a label to about `chars` characters, with "…" (SVG text doesn't do it itself). */
function clip(label: string, chars: number): string {
  const n = Math.max(4, Math.floor(chars))
  return label.length > n ? `${label.slice(0, n - 1)}…` : label
}
