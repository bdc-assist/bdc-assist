import cytoscape, { type LayoutOptions, type StylesheetJson } from 'cytoscape'

import { bridges, sharedOnly } from './bridges'
import { collapseVersions } from './collapse'
import { columnPositions } from './columns'
import { connections } from './connections'
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
  /** Show only what two or more concepts share (see bridges/sharedOnly; default false). */
  sharedOnly?: boolean
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
  /** Change options for the current graph: a new layout only rearranges the nodes;
   * collapseVersions and sharedOnly redraw it (and clear the selection). */
  setOptions(options: Partial<GraphOptions>): void
  /** Mark a node selected (null: none), e.g. one picked in a list next to the graph.
   * Ids are as drawn (collapsed or not). Doesn't call onSelect. */
  select(id: string | null): void
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
  // variables run from low to high along their weight (related_concepts_count)
  variableLow: ['--kg-variable-low', '#93c5fd'],
  variableHigh: ['--kg-variable-high', '#1e40af'],
  study: ['--kg-study', '#059669'],
  edge: ['--kg-edge', '#cbd5e1'],
  label: ['--kg-label', '#475569'],
  selected: ['--kg-selected', '#0f172a'],
  background: ['--kg-background', '#ffffff'], // behind highlighted nodes' labels
} as const

const PADDING = 16

/**
 * Draw `graph` into `container` (which needs a height). Variables are coloured from
 * --kg-variable-low to --kg-variable-high the more other concepts they link to
 * (related_concepts_count); nodes connecting two or more concepts get an amber halo.
 * Clicking a
 * node (or select()) highlights what it's connected to (see connections) and fades
 * the rest; clicking the background clears it.
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
  cy.on('tap', 'node', (e) => {
    highlight(e.target.id())
    opts.onSelect?.(shown.get(e.target.id()) ?? null)
  })
  cy.on('tap', (e) => {
    if (e.target !== cy) return
    highlight(null)
    opts.onSelect?.(null)
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
    if (opts.sharedOnly) drawn = sharedOnly(drawn) // after merging: it can create bridges
    shown = new Map(drawn.nodes.map((n) => [n.id, n]))
    const shared = bridges(drawn)
    cy.batch(() => {
      cy.elements().remove()
      cy.add(toElements(drawn))
      // nodes that connect two or more concepts get a halo (`bridge`)
      cy.nodes().forEach((n) => void n.toggleClass('bridge', shared.has(n.id())))
    })
    arrange()
  }

  // the node's connections get `focus`, everything else `faded`; null clears both
  function highlight(id: string | null) {
    const c = id ? connections(drawn, id) : null
    cy.batch(() => {
      cy.elements().removeClass('focus faded')
      if (!c?.nodes.size) return
      cy.elements().forEach((el) => {
        const elId = el.id()
        const on = el.isNode() ? c.nodes.has(elId) : c.edges.has(elId)
        el.addClass(on ? 'focus' : 'faded')
      })
    })
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
      // without a box, breadthfirst spreads over cy.extent(): the visible area at the
      // current zoom. Zoomed out to fit, that's bigger than the container, so every
      // redraw (e.g. toggling sharedOnly) would spread wider and fit smaller. Use the
      // container's own size (a fallback while it's hidden, e.g. in the list view).
      boundingBox: { x1: 0, y1: 0, w: container.clientWidth || 600, h: container.clientHeight || 300 },
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
      const changed = (k: 'collapseVersions' | 'sharedOnly') => o[k] !== undefined && o[k] !== opts[k]
      const redraw = changed('collapseVersions') || changed('sharedOnly')
      const relayout = o.layout !== undefined && o.layout !== opts.layout
      opts = { ...opts, ...o }
      cy.userZoomingEnabled(opts.zoomGestures === true)
      if (redraw) {
        opts.onSelect?.(null)
        draw()
      } else if (relayout) arrange()
    },
    select(id) {
      cy.batch(() => {
        cy.$(':selected').unselect()
        if (id) cy.getElementById(id).select()
      })
      highlight(id)
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
      style: { 'background-color': (n) => mix(color('variableLow'), color('variableHigh'), n.data('weight') ?? 1) },
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
    {
      // connects two or more concepts: a soft halo in the concept colour
      selector: 'node.bridge',
      style: {
        'underlay-color': color('concept'),
        'underlay-padding': 6,
        'underlay-opacity': 0.35,
        'underlay-shape': 'ellipse',
      },
    },
    { selector: 'node.bridge[type = "study"]', style: { 'underlay-shape': 'round-rectangle' } },
    { selector: '.faded', style: { opacity: 0.15 } },
    {
      // highlighted: whole labels (no "…"), on a backing so they stay readable over edges
      selector: 'node.focus',
      style: {
        'text-wrap': 'none',
        'z-index': 10,
        'text-background-color': color('background'),
        'text-background-opacity': 0.85,
        'text-background-padding': '2px',
        'text-background-shape': 'roundrectangle',
      },
    },
    { selector: 'edge.focus', style: { 'line-color': color('label'), width: 1.5, 'z-index': 9 } },
    { selector: 'node:selected', style: { 'border-width': 3, 'border-color': color('selected') } },
  ]
}

/** A colour t (0..1) of the way from a to b, mixed in RGB (fine within one hue).
 * Takes toRgb's "rgb(r, g, b)" or a "#rrggbb" fallback. */
function mix(a: string, b: string, t: number): string {
  const [x, y] = [a, b].map(channels)
  const c = x.map((v, i) => Math.round(v + (y[i] - v) * Math.min(1, Math.max(0, t))))
  return `rgb(${c.join(', ')})`
}

function channels(color: string): number[] {
  const rgb = /rgb\((\d+), (\d+), (\d+)\)/.exec(color)
  if (rgb) return rgb.slice(1).map(Number)
  const hex = /^#([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(color)
  return hex ? hex.slice(1).map((h) => parseInt(h, 16)) : [0, 0, 0]
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
