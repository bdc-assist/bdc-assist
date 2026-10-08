# `src/kg`: knowledge graph views

Draws the knowledge graph BDC Assist returns with its answers: the Dug concepts an
answer is about, the study variables linked to them, and the studies those variables
belong to. Two views (a node-link graph and a Sankey "flow") plus plain helpers for
building your own UI (lists, tables, links).

It is self-contained so it can be lifted into another system:

- no framework: plain TypeScript and the DOM; React, Vue, Angular or plain HTML all
  call it the same way
- no imports from the rest of this app
- dependencies: [`cytoscape`](https://js.cytoscape.org) (graph view) and
  [`d3-sankey`](https://github.com/d3/d3-sankey) (flow view)
- styling through CSS custom properties, so the host restyles it from its own CSS

The demo client's wrapper, [`components/bdc/knowledge-graph.tsx`](../components/bdc/knowledge-graph.tsx),
is a working example of everything below (React, but only as glue).

## The data

The API sends the agent's knowledge graphs as `kg`: in the `sources` event of
`POST /chat/stream` (as soon as the agent is done), in its `done` event, and in
`POST /chat`. It's a list, one graph per tool call, attached by the Dug interceptor
(`examples/bdc/interceptors.py`):

```ts
type KgWireEntry = {
  tool: string; args?: object; label?: string
  nodes: { id: string; name?: string; type?: string; category?: string;
           description?: string; attributes?: { related_concepts_count?: number } }[]
  edges: { subject: string; object: string; predicate?: string }[]
}
```

`fromKgList(kg)` (`wire.ts`) merges them into the one graph the views draw, or `null`
when there's nothing to draw. A node's role comes from `type` if it names one, else
from `category` (`"Study"`, `"StudyVariable"`; anything else is a concept):

```ts
type KgGraph = { nodes: KgNode[]; edges: KgEdge[] }

type KgNode = {
  id: string                       // concept CURIE, dbGaP variable or study accession (with version)
  label: string
  type: 'concept' | 'variable' | 'study'
  concept_type?: string            // concepts: Dug's biolink category, verbatim
  related_concepts_count?: number  // variables: how many other concepts it links to
  versions?: string[]              // after collapseVersions: the versioned ids merged into this node
}

type KgEdge = { source: string; target: string; predicate?: string }
// variable → concept, variable → study; concept → concept with Dug's predicate
```

## Quick start

```ts
import { mountGraph } from './kg/mount'
import { fromKgList } from './kg/wire'

const graph = fromKgList(doneEvent.kg)
if (graph) {
  const view = mountGraph(document.querySelector('#kg')!, graph, {
    layout: 'radial',
    onFocus: (focus) => showDetails(focus),  // your UI
  })
  // later: view.update(otherGraph), view.destroy()
}
```

The container needs a size (e.g. `height: 18rem`). Swap `mountGraph` for `mountFlow`
to get the Sankey view; both have the same interface.

## Views

Both views take the same options and return the same interface (`view.ts`), so a host
can switch between them without caring which one is showing.

```ts
type KgViewOptions = {
  collapseVersions?: boolean  // merge a study's or variable's dbGaP releases into one node (default true)
  sharedOnly?: boolean        // only studies with variables on two or more concepts (default false)
  onFocus?: (focus: KgFocus | null) => void  // the user picked something; null: clicked the background
}

type KgView = {
  update(graph, options?)  // draw another graph, or the same with other options
  setOptions(options)      // change options for the current graph
  focus(focus | null)      // show a focus picked elsewhere (another view, your list); doesn't call onFocus
  resize()                 // after the container changed size, e.g. was hidden and shown
  refreshStyle()           // re-read the --kg-* colours, e.g. after a theme change
  destroy()
}
```

**Focus.** What the user picked, the same in every view:

```ts
type KgFocus = { node: string } | { concept: string; study: string }
```

A node (concept, study or variable), or a concept × study pair: "what does this study
have on this concept?" (a band in the flow view). The view highlights what it connects
(see `focusConnections`) and fades the rest. To keep several views and your own UI in
step, keep one focus in the host: on any view's `onFocus`, store it and call `focus()`
on the others.

### `mountGraph(container, graph, options)` (`mount.ts`)

A node-link graph (Cytoscape). Concepts, variables and studies are drawn as distinct
shapes and colours; variables run light to dark blue with `related_concepts_count`;
studies (or variables) connecting two or more concepts get an amber halo.

Extra options:

| Option | |
|---|---|
| `layout` | `'radial'` (default: concepts in the middle, variables around, studies outside), `'force'` (force-directed, turned to fit the container), `'columns'` (concepts \| variables \| studies). All listed in `KG_LAYOUTS` |
| `zoomGestures` | `false` (default: the wheel scrolls the page), `'modifier'` (Ctrl/⌘ + wheel zooms; trackpad pinch counts), `true` (the wheel always zooms). Use `'modifier'` where a page scrolls around the graph |
| `onZoomHint` | `'modifier'` mode: called when the wheel turns over the graph without Ctrl/⌘, so you can say how to zoom |

Extra methods: `zoomBy(factor)` and `fit()`, for your own zoom buttons. Hovering a
node shows its full label as the browser's tooltip.

### `mountFlow(container, graph, options)` (`flow.ts`)

A Sankey diagram (d3-sankey layout, plain SVG): concepts on the left, studies on the
right, each concept → study band as wide as the study's variables on that concept.
Clicking a band focuses its concept × study pair. Variables aren't drawn, but a
variable focus highlights its band. It lays out for the container's size, so call
`resize()` (or use a `ResizeObserver`) when that changes.

## Colours

Set these on the container or any ancestor; any CSS colour works (including
`oklch()` and `var()`). Unset ones use the defaults.

| Property | Default | Used for |
|---|---|---|
| `--kg-concept` | `#d97706` | concepts, the shared-study halo |
| `--kg-variable-low` | `#93c5fd` | variables with the fewest related concepts |
| `--kg-variable-high` | `#1e40af` | variables with the most; flow bands are a mix of the two |
| `--kg-study` | `#059669` | studies |
| `--kg-edge` | `#cbd5e1` | graph edges |
| `--kg-label` | `#475569` | labels |
| `--kg-selected` | `#0f172a` | the outline of the focused node |

## Helpers for your own UI

All plain functions of a `KgGraph`. Apply `collapseVersions` (and `sharedOnly`, if
used) first, so ids match what the views draw.

| Function | File | |
|---|---|---|
| `collapseVersions(graph)`, `baseId(id)` | `collapse.ts` | merge dbGaP releases (`phs000007.v34.p15` + `.v31.p12` → `phs000007`, with `versions`) |
| `bridges(graph)`, `sharedOnly(graph)` | `bridges.ts` | the nodes connecting two or more concepts; the graph reduced to them |
| `studyList(graph)` | `list.ts` | studies → concepts → variables, shared studies first: for a list or table view, or a text alternative to the graph |
| `nodeLinks(node, graph)`, `conceptLink(id)` | `links.ts` | pages for a node: dbGaP study and variable pages (one per release; a variable without an accession gets its study, with a `note`); concepts by CURIE prefix (MONDO, EFO, UMLS, GTOPDB, otherwise bioregistry.io), marked "(login)" where an account is needed |
| `focusConnections(graph, focus)`, `pairVariables(graph, concept, study)` | `focus.ts` | what a focus lights up; a pair's variables |
| `flowData(graph)` | `flow.ts` | the flow's concept → study links with their variables, for your own chart |
| `fromKgList(kg)` | `wire.ts` | the API's per-call graphs merged into one |
| `variableWeights(graph)` | `elements.ts` | `related_concepts_count` scaled to 0..1 within the graph |

## Things to know

- **Give the container a size, and don't position it with `position: absolute`.**
  Cytoscape adds `position: relative` to its container from an unlayered style sheet,
  which wins over layered CSS such as Tailwind's utilities. Size it with a height
  inside a positioned wrapper instead.
- **Hidden containers:** a view mounted in a hidden container lays out for a fallback
  size; call `resize()` once it's shown.
- **Variable IDs that aren't `phv` accessions** (e.g. `phs003708_MHASTH.v1.p1`, study +
  variable name): dbGaP lists only a few variables for some studies, so Dug takes their
  data dictionary from PIC-SURE, and there's no dbGaP variable page. `nodeLinks` gives
  their study's page instead, with `note: NO_VARIABLE_ACCESSION` ("No dbGaP variable
  accession available") so you can say why.
- **The force layout is recomputed** on every redraw, so it differs from one draw to
  the next.
