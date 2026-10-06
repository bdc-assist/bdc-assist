import cytoscape, { type BreadthFirstLayoutOptions, type StylesheetJson } from 'cytoscape'

import { collapseVersions } from './collapse'
import { toElements } from './elements'
import type { KgGraph, KgNode } from './types'

export type GraphOptions = {
  /** Merge the releases of a study or variable into one node (default true). */
  collapseVersions?: boolean
  /** A node was clicked (null: the background). The host decides what to show. */
  onSelect?: (node: KgNode | null) => void
}

export type GraphView = {
  /** Draw another graph, or the same with changed options. */
  update(graph: KgGraph, options?: Partial<GraphOptions>): void
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
 * Draw `graph` into `container` (which needs a height), laid out radially:
 * concepts in the middle, their variables around them, studies on the outside.
 * No framework required; React, Vue or plain HTML hosts all call this the same way.
 */
export function mountGraph(container: HTMLElement, graph: KgGraph, options: GraphOptions = {}): GraphView {
  let opts: GraphOptions = { collapseVersions: true, ...options }
  let shown = new Map<string, KgNode>() // id → node as drawn (collapsed or not)

  const cy = cytoscape({
    container,
    style: styleFor(container),
    // wheel zoom would hijack scrolling of the page or chat the graph sits in
    userZoomingEnabled: false,
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

  function draw(g: KgGraph) {
    const drawn = opts.collapseVersions ? collapseVersions(g) : g
    shown = new Map(drawn.nodes.map((n) => [n.id, n]))
    cy.batch(() => {
      cy.elements().remove()
      cy.add(toElements(drawn))
    })
    const layout: BreadthFirstLayoutOptions = {
      name: 'breadthfirst',
      roots: cy.nodes('[type = "concept"]').map((n) => n.id()),
      circle: true,
      depthSort: (a, b) => String(a.data('order')).localeCompare(String(b.data('order'))),
      padding: PADDING,
      animate: false,
    }
    cy.layout(layout).run()
  }
  draw(graph)

  return {
    update(g, o) {
      if (o) opts = { ...opts, ...o }
      opts.onSelect?.(null) // the selected node may be gone or renamed
      draw(g)
    },
    resize() {
      cy.resize()
      cy.fit(undefined, PADDING)
    },
    refreshStyle() {
      cy.style(styleFor(container))
    },
    destroy() {
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
    { selector: 'node[type = "variable"]', style: { 'background-color': color('variable') } },
    {
      selector: 'node[type = "study"]',
      style: { 'background-color': color('study'), shape: 'round-rectangle', width: 16, height: 16 },
    },
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
