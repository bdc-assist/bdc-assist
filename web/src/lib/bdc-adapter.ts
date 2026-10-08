import type { ChatModelAdapter, ThreadMessage } from '@assistant-ui/react'

import type { KgGraph } from '@/kg/types'
import { fromKgList, kgParts, type KgPart } from '@/kg/wire'
import { readSSE } from '@/lib/sse'

// Wire format of POST /chat/stream (see stream_chat in r_assist/api.py).
export type Source = { title: string; link: string; type: string }
export type Sources = Record<string, Source[]> // {"bdc-doc": [...]}, deduplicated

type StreamEvent =
  | { type: 'node'; node: string }
  | { type: 'status'; text: string }
  | { type: 'token'; text: string }
  | { type: 'reset' }
  | { type: 'sources'; sources: Sources; sources_md: string; kg?: unknown } // kg: see fromKgList
  | {
      type: 'done'
      answer: string
      blocked: boolean // the input was refused, or the answer was (output guardrail)
      topics: string[]
      followups: string[]
      sources: Sources
      sources_md: string
      kg?: unknown
      mcp_errors?: string[] // "server: error" per MCP server currently unavailable
    }
  | { type: 'error' } // the run failed; last event, no done

/** What we keep on each assistant message as metadata.custom. Plain JSON,
 * so a thread can be persisted later as-is. */
export type BdcMessageMeta = {
  node: string | null // graph node currently running; null once done
  status: string | null // latest tool-call status; cleared by reset and new nodes
  sources: Sources | null // null until the agent reports them
  graph: KgGraph | null // the knowledge graph (kg) merged; null when there is none (yet)
  graphParts: KgPart[] // the same per tool call, labelled, to show one on its own
  unavailable: string[] // MCP servers that were down, by name (from mcp_errors)
  followups: string[]
  blocked: boolean // input guardrail refused the question
  rejected: boolean // output guardrail replaced the streamed draft
  done: boolean
}

/** How the answer is shown before the server has checked it:
 * - 'stream': the draft streams in as written
 * - 'after-check': placeholder bars grow with the draft; the text appears at done */
export type Reveal = 'stream' | 'after-check'

// Names of the `data` message parts the adapter emits, one renderer each.
export const STATUS_PART = 'bdc-status'
export const SOURCES_PART = 'bdc-sources'
export const BLOCKED_PART = 'bdc-blocked'
export const DRAFT_PART = 'bdc-draft'
export const REJECTED_PART = 'bdc-rejected'
export const GRAPH_PART = 'bdc-graph' // data: GraphPartData

/** The graph part's data: the merged graph, and each call's graph when there are several. */
export type GraphPartData = { graph: KgGraph; parts: KgPart[] }
export const FOLLOWUPS_PART = 'bdc-followups' // data: string[]
export const UNAVAILABLE_PART = 'bdc-unavailable' // data: string[] (MCP server names)

export type StatusPartData = { node: string | null; status: string | null }
export type DraftPartData = { words: number }

type StreamState = {
  text: string // the draft while running; the final answer once done
  streaming: boolean // tokens arriving since the last node/status/reset
  meta: BdcMessageMeta
}

const data = (name: string, data: object) => ({ type: 'data' as const, name, data })

/** The message content for the current state: the text first, then data parts
 * for message-parts.tsx to draw. History only sends text parts, so the data
 * parts never reach the server. */
export function toContent({ text, streaming, meta }: StreamState, reveal: Reveal) {
  const hold = reveal === 'after-check' && !meta.done
  const parts = []
  parts.push({ type: 'text' as const, text: hold ? '' : text })
  if (hold && text) {
    parts.push(data(DRAFT_PART, { words: text.split(/\s+/).filter(Boolean).length } satisfies DraftPartData))
  }
  // before the status line, so the status stays at the bottom while later nodes run
  if (!hold && meta.graph) {
    const graphData: GraphPartData = { graph: meta.graph, parts: meta.graphParts.length > 1 ? meta.graphParts : [] }
    parts.push(data(GRAPH_PART, graphData))
  }
  // streamed tokens are the progress, so the node label steps aside for them;
  // with the text held back, the label stays
  if (meta.status || (meta.node && (hold || !streaming))) {
    parts.push(data(STATUS_PART, { node: meta.node, status: meta.status } satisfies StatusPartData))
  }
  if (!hold && meta.sources && Object.values(meta.sources).some((s) => s.length)) {
    parts.push(data(SOURCES_PART, meta.sources))
  }
  // only worth saying if the user watched the draft stream in
  if (reveal === 'stream' && meta.rejected) parts.push(data(REJECTED_PART, {}))
  if (meta.blocked) parts.push(data(BLOCKED_PART, {}))
  if (meta.done && meta.unavailable.length) parts.push(data(UNAVAILABLE_PART, meta.unavailable))
  // the server sends none for a blocked question; checked anyway, they'd make no sense there
  if (meta.done && !meta.blocked && meta.followups.length) parts.push(data(FOLLOWUPS_PART, meta.followups))
  return parts
}

const squash = (s: string) => s.replace(/\s+/g, ' ').trim()

/** done carries no "rejected" flag, so infer it: a passed answer is the draft,
 * possibly with disclaimers appended; a rejected one is the canned REJECT reply. */
const replacedDraft = (draft: string, answer: string) =>
  squash(draft) !== '' && !squash(answer).startsWith(squash(draft))

const textOf = (m: ThreadMessage) =>
  m.content
    .filter((p) => p.type === 'text')
    .map((p) => p.text)
    .join('')

/** The server is stateless: send the last user message as `input` and every
 * earlier text turn as `chat_history`. */
export function toRequest(messages: readonly ThreadMessage[]) {
  const last = messages.at(-1)
  if (last?.role !== 'user') throw new Error('last message must be from the user')
  const chat_history = messages
    .slice(0, -1)
    .filter((m) => m.role === 'user' || m.role === 'assistant')
    .map((m) => ({ role: m.role, content: textOf(m) }))
    .filter((m) => m.content)
  return { input: textOf(last), chat_history }
}

export type BdcAdapterOptions = {
  reveal?: Reveal
  fetchImpl?: typeof fetch
}

export function createBdcAdapter(
  apiUrl: string,
  { reveal = 'stream', fetchImpl = fetch }: BdcAdapterOptions = {},
): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const state: StreamState = {
        text: '',
        streaming: false,
        meta: {
          node: null,
          status: null,
          sources: null,
          graph: null,
          graphParts: [],
          unavailable: [],
          followups: [],
          blocked: false,
          rejected: false,
          done: false,
        },
      }
      const { meta } = state
      let failed = false // the server sent {"type": "error"}
      // the runtime replaces the message with each yield, so always send it all
      const snapshot = () => ({
        content: toContent(state, reveal),
        metadata: { custom: { ...meta } },
      })

      let resp: Response
      try {
        resp = await fetchImpl(`${apiUrl}/chat/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(toRequest(messages)),
          signal: abortSignal,
        })
      } catch (err) {
        if (abortSignal.aborted) return
        console.error('bdc-assist fetch failed', err)
        throw new Error(`Can't reach BDC Assist at ${apiUrl}. Is the API running?`)
      }
      if (!resp.ok || !resp.body) {
        throw new Error(`BDC Assist ran into a problem (HTTP ${resp.status}). Please try again.`)
      }

      try {
        for await (const data of readSSE(resp.body)) {
          const ev = data as StreamEvent
          switch (ev.type) {
            case 'node':
              state.streaming = false
              meta.node = ev.node
              meta.status = null
              break
            case 'status':
              state.streaming = false
              meta.status = ev.text
              break
            case 'token':
              // the tool status stays: a model often says "Let me look that up"
              // in the same chunk as the tool call, then waits on the tool
              state.streaming = true
              state.text += ev.text
              break
            case 'reset':
              state.text = '' // new model turn: only the last response counts
              state.streaming = false
              meta.status = null // the tool finished
              break
            case 'sources':
              // agent finished: sources and graphs are final
              meta.sources = ev.sources
              meta.graph = fromKgList(ev.kg)
              meta.graphParts = kgParts(ev.kg)
              break
            case 'done': {
              // authoritative: rejects, disclaimers and canned replies replace
              // the streamed text, and a rejected answer has no sources or graph.
              // blocked covers both guardrails; a streamed draft that was replaced
              // means the output one (a reject), none means the input was refused.
              const replaced = replacedDraft(state.text, ev.answer)
              meta.rejected = replaced
              meta.blocked = ev.blocked && !replaced
              state.text = ev.answer
              meta.sources = ev.sources
              meta.graph = fromKgList(ev.kg)
              meta.graphParts = kgParts(ev.kg)
              meta.unavailable = [...new Set((ev.mcp_errors ?? []).map((e) => e.split(':')[0].trim()))]
              meta.followups = ev.followups
              meta.node = null
              meta.status = null
              meta.done = true
              break
            }
            case 'error':
              failed = true
              break
            default:
              continue // unknown event type: ignore, don't re-render
          }
          if (failed) break
          yield snapshot()
        }
      } catch (err) {
        if (abortSignal.aborted) return
        console.error('bdc-assist stream failed', err)
        throw new Error('Lost the connection to BDC Assist mid-answer. Please try again.')
      }
      if (failed) throw new Error('BDC Assist ran into a problem answering. Please try again.')
      if (!meta.done) throw new Error('BDC Assist stopped before finishing the answer. Please try again.')
    },
  }
}
