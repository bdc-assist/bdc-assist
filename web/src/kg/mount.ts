import cytoscape, { type LayoutOptions, type StylesheetJson } from 'cytoscape'

import { collapseVersions } from './collapse'
import { columnPositions } from './columns'
import { toElements } from './elements'
import { orient } from './orient'
import type { KgGraph, KgNode } from './types'

/**
 * - radial: concepts in the middle, their variables around them, studies outside
 * - columns: a left-to-right flow, concepts | variables | studies
 * - force: a force-directed layout (Cytoscape's cose); clusters form on their own
 */
export const KG_LAYOUTS = ['radial', 'columns', 'force'] as const
export type KgLayout = (typeof KG_LAYOUTS)[number]

export type GraphOptions = {
  /** Merge the releases of a study or variable into one node (default true). */
  collapseVersions?: boolean
  /** How to arrange the nodes (default 'radial'). */
  layout?: KgLayout
  /** Zoom with the mouse wheel and pinch. false (default): no, the wheel scrolls
   * the page. 'modifier': only with Ctrl/⌘ held (trackpad pinch counts: browsers
   * send it as Ctrl + wheel), so a plain wheel still scrolls the page around the
   * graph. true: always. zoomBy works either way. */
  zoomGestures?: boolean | 'modifier'
  /** 'modifier' mode: the wheel turned over the graph without Ctrl/⌘ held, so the
   * host can say how to zoom. */
  onZoomHint?: () => void
  /** A node was clicked (null: the background). The host decides what to show. */
  onSelect?: (node: KgNode | null) => void
}

export type GraphView = {
  /** Draw another graph, or the same with changed options. */
  update(graph: KgGraph, options?: Partial<GraphOptions>): void
  /** Change options for the current graph: a new layout only rearranges the nodes. */
  setOptions(options: Partial<GraphOptions>): void
  /** Zoom in (factor > 1) or out (< 1) around the middle of the view. */
  zoomBy(factor: number): void
  /** Show the whole graph. */
  fit(): void
  /** Call after the container changed size (e.g. was hidden, then shown). */
  resize(): void
  /** Re-read the --kg-* colours (e.g. after a theme change). */
  refreshStyle(): void
  destroy(): void
}

// Colours come from CSS custom properties on the container (or any ancestor), so
// a host restyles the graph from its own CSS. Any CSS colour works: Cytoscape
// can't parse e.g. oklch() itself, so toRgb converts through a canvas.
const COLORS = {
  concept: ['--kg-concept', '#d97706'],
  variable: ['--kg-variable', '#2563eb'],
  study: ['--kg-study', '#059669'],
  edge: ['--kg-edge', '#cbd5e1'],
  label: ['--kg-label', '#475569'],
  selected: ['--kg-selected', '#0f172a'],
} as const

const PADDING = 16

/**
 * Draw `graph` into `container` (which needs a height). Variables are drawn more
 * opaque the more other concepts they link to (related_concepts_count).
 * No framework required; React, Vue or plain HTML hosts all call this the same way.
 */
export function mountGraph(container: HTMLElement, graph: KgGraph, options: GraphOptions = {}): GraphView {
  let opts: GraphOptions = { collapseVersions: true, layout: 'radial', ...options }
  let current = graph
  let shown = new Map<string, KgNode>() // id → node as drawn (collapsed or not)

  const cy = cytoscape({
    container,
    style: styleFor(container),
    userZoomingEnabled: opts.zoomGestures === true,
    minZoom: 0.1,
    maxZoom: 4,
    boxSelectionEnabled: false,
  })
  cy.on('tap', 'node', (e) => opts.onSelect?.(shown.get(e.target.id()) ?? null))
  cy.on('tap', (e) => {
    if (e.target === cy) opts.onSelect?.(null)
  })
  // labels on the canvas are cut short ("…"): show the whole one as the browser's
  // own tooltip, and a pointer, since nodes are clickable
  cy.on('mouseover', 'node', (e) => {
    container.title = String(e.target.data('label'))
    container.style.cursor = 'pointer'
  })
  cy.on('mouseout', 'node', () => {
    container.removeAttribute('title')
    container.style.cursor = ''
  })

  // 'modifier' zoom: Cytoscape's own wheel zoom is off, so handle Ctrl/⌘ + wheel here
  // (non-passive, to keep the browser from zooming the page instead)
  function onWheel(e: WheelEvent) {
    if (opts.zoomGestures !== 'modifier') return
    if (!(e.ctrlKey || e.metaKey)) return opts.onZoomHint?.()
    e.preventDefault()
    const lines = e.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16 : 1 // Firefox: lines, not pixels
    const box = container.getBoundingClientRect()
    cy.zoom({
      level: cy.zoom() * Math.exp(-e.deltaY * lines * 0.002),
      renderedPosition: { x: e.clientX - box.left, y: e.clientY - box.top },
    })
  }
  container.addEventListener('wheel', onWheel, { passive: false })

  let drawn: KgGraph = graph
  function draw() {
    drawn = opts.collapseVersions ? collapseVersions(current) : current
    shown = new Map(drawn.nodes.map((n) => [n.id, n]))
    cy.batch(() => {
      cy.elements().remove()
      cy.add(toElements(drawn))
    })
    arrange()
  }

  function arrange() {
    // labels sit beside the nodes in columns (concepts' on the left), below otherwise
    cy.nodes().toggleClass('columns', opts.layout === 'columns')
    const layout = cy.layout(layoutOptions(opts.layout ?? 'radial'))
    // force layouts settle at any angle: turn them to match the container's shape
    if (opts.layout === 'force') layout.one('layoutstop', orientToContainer)
    layout.run()
  }

  function orientToContainer() {
    const nodes = cy.nodes()
    const turned = orient(
      nodes.map((n) => ({ ...n.position() })),
      container.clientWidth >= container.clientHeight,
    )
    cy.batch(() => nodes.forEach((n, i) => void n.position(turned[i])))
    cy.fit(undefined, PADDING)
  }

  function layoutOptions(layout: KgLayout): LayoutOptions {
    if (layout === 'columns') {
      const pos = columnPositions(drawn)
      return { name: 'preset', positions: Object.fromEntries(pos), padding: PADDING, animate: false }
    }
    if (layout === 'force') {
      return { name: 'cose', padding: PADDING, animate: false, randomize: true, nodeDimensionsIncludeLabels: true }
    }
    return {
      name: 'breadthfirst',
      roots: cy.nodes('[type = "concept"]').map((n) => n.id()),
      circle: true,
      depthSort: (a, b) => String(a.data('order')).localeCompare(String(b.data('order'))),
      padding: PADDING,
      animate: false,
    }
  }
  draw()

  return {
    update(g, o) {
      if (o) opts = { ...opts, ...o }
      current = g
      opts.onSelect?.(null) // the selected node may be gone or renamed
      draw()
    },
    setOptions(o) {
      const redraw = o.collapseVersions !== undefined && o.collapseVersions !== opts.collapseVersions
      const relayout = o.layout !== undefined && o.layout !== opts.layout
      opts = { ...opts, ...o }
      cy.userZoomingEnabled(opts.zoomGestures === true)
      if (redraw) {
        opts.onSelect?.(null)
        draw()
      } else if (relayout) arrange()
    },
    zoomBy(factor) {
      cy.zoom({
        level: cy.zoom() * factor,
        renderedPosition: { x: container.clientWidth / 2, y: container.clientHeight / 2 },
      })
    },
    fit() {
      cy.fit(undefined, PADDING)
    },
    resize() {
      cy.resize()
      cy.fit(undefined, PADDING)
    },
    refreshStyle() {
      cy.style(styleFor(container))
    },
    destroy() {
      container.removeEventListener('wheel', onWheel)
      cy.destroy()
    },
  }
}

function styleFor(el: HTMLElement): StylesheetJson {
  const css = getComputedStyle(el)
  const color = (key: keyof typeof COLORS) => {
    const [name, fallback] = COLORS[key]
    const value = css.getPropertyValue(name).trim()
    return (value && toRgb(value)) || fallback
  }
  return [
    {
      selector: 'node',
      style: {
        label: 'data(label)',
        'font-family': css.fontFamily,
        'font-size': 10,
        color: color('label'),
        'text-valign': 'bottom',
        'text-margin-y': 3,
        'text-wrap': 'ellipsis',
        'text-max-width': '110px',
        width: 12,
        height: 12,
      },
    },
    {
      selector: 'node[type = "concept"]',
      style: {
        'background-color': color('concept'),
        width: 24,
        height: 24,
        'font-size': 12,
        'font-weight': 'bold',
        'text-max-width': '200px',
      },
    },
    {
      selector: 'node[type = "variable"]',
      style: { 'background-color': color('variable'), 'background-opacity': (n) => 0.25 + 0.75 * (n.data('weight') ?? 1) },
    },
    {
      selector: 'node[type = "study"]',
      style: { 'background-color': color('study'), shape: 'round-rectangle', width: 16, height: 16 },
    },
    {
      selector: 'node.columns',
      style: { 'text-valign': 'center', 'text-halign': 'right', 'text-margin-x': 5, 'text-margin-y': 0, 'text-max-width': '200px' },
    },
    { selector: 'node.columns[type = "concept"]', style: { 'text-halign': 'left', 'text-margin-x': -5 } },
    { selector: 'edge', style: { width: 1, 'line-color': color('edge'), 'curve-style': 'straight' } },
    { selector: 'node:selected', style: { 'border-width': 3, 'border-color': color('selected') } },
  ]
}

let ctx: CanvasRenderingContext2D | null | undefined

/** Any CSS colour → "rgb(r, g, b)", or null if the browser doesn't understand it. */
function toRgb(value: string): string | null {
  ctx ??= document.createElement('canvas').getContext('2d', { willReadFrequently: true })
  if (!ctx) return null
  const sentinel = '#010203'
  ctx.fillStyle = sentinel
  ctx.fillStyle = value // ignored if invalid
  if (ctx.fillStyle === sentinel && value.toLowerCase() !== sentinel) return null
  ctx.clearRect(0, 0, 1, 1)
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
  return `rgb(${r}, ${g}, ${b})`
}
