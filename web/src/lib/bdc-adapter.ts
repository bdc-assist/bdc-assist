import type { ChatModelAdapter, ThreadMessage } from '@assistant-ui/react'

import { readSSE } from '@/lib/sse'

// Wire format of POST /chat/stream (see stream_chat in bdc_assist/api.py).
export type Source = { title: string; link: string; type: string }
export type Sources = Record<string, Source[]> // {"bdc-doc": [...]}, deduplicated

type StreamEvent =
  | { type: 'node'; node: string }
  | { type: 'status'; text: string }
  | { type: 'token'; text: string }
  | { type: 'reset' }
  | { type: 'sources'; sources: Sources; sources_md: string }
  | {
      type: 'done'
      answer: string
      blocked: boolean
      topics: string[]
      followups: string[]
      sources: Sources
      sources_md: string
    } // tool_results is also sent; deliberately not kept

/** What we keep on each assistant message as metadata.custom. Plain JSON,
 * so a thread can be persisted later as-is. */
export type BdcMessageMeta = {
  node: string | null // graph node currently running; null once done
  status: string | null // latest tool-call status; cleared by reset and new nodes
  sources: Sources | null // null until the agent reports them
  followups: string[]
  blocked: boolean
  done: boolean
}

// Names of the `data` message parts the adapter emits, one renderer each.
export const STATUS_PART = 'bdc-status'
export const SOURCES_PART = 'bdc-sources'
export const BLOCKED_PART = 'bdc-blocked'

export type StatusPartData = { node: string | null; status: string | null }

/** Extra parts after the text: progress while running, then sources and the
 * blocked notice once known. History only sends text parts, so these never
 * reach the server. */
function dataParts(meta: BdcMessageMeta, streaming: boolean) {
  const parts = []
  // while tokens flow they are the progress, so the node label steps aside
  if (meta.status || (meta.node && !streaming)) {
    parts.push({
      type: 'data' as const,
      name: STATUS_PART,
      data: { node: meta.node, status: meta.status } satisfies StatusPartData,
    })
  }
  if (meta.sources && Object.values(meta.sources).some((s) => s.length)) {
    parts.push({ type: 'data' as const, name: SOURCES_PART, data: meta.sources })
  }
  if (meta.blocked) parts.push({ type: 'data' as const, name: BLOCKED_PART, data: {} })
  return parts
}

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

export function createBdcAdapter(
  apiUrl: string,
  fetchImpl: typeof fetch = fetch,
): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      let text = ''
      let streaming = false // tokens arriving since the last node/status/reset
      const meta: BdcMessageMeta = {
        node: null,
        status: null,
        sources: null,
        followups: [],
        blocked: false,
        done: false,
      }
      // the runtime replaces the message with each yield, so always send it all.
      // metadata is the record; the data parts are what message-parts.tsx draws
      const snapshot = () => ({
        content: [
          { type: 'text' as const, text },
          ...dataParts(meta, streaming),
        ],
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
        throw new Error(`Can't reach bdc-assist at ${apiUrl} (${err})`)
      }
      if (!resp.ok || !resp.body) {
        throw new Error(`bdc-assist returned HTTP ${resp.status}`)
      }

      try {
        for await (const data of readSSE(resp.body)) {
          const ev = data as StreamEvent
          switch (ev.type) {
            case 'node':
              streaming = false
              meta.node = ev.node
              meta.status = null
              break
            case 'status':
              streaming = false
              meta.status = ev.text
              break
            case 'token':
              // the tool status stays: a model often says "Let me look that up"
              // in the same chunk as the tool call, then waits on the tool
              streaming = true
              text += ev.text
              break
            case 'reset':
              text = '' // new model turn: only the last response counts
              streaming = false
              meta.status = null // the tool finished
              break
            case 'sources':
              meta.sources = ev.sources // agent finished: sources are final
              break
            case 'done':
              // authoritative: rejects, disclaimers and canned replies replace
              // the streamed text, and a rejected answer has no sources
              text = ev.answer
              meta.sources = ev.sources
              meta.followups = ev.followups
              meta.blocked = ev.blocked
              meta.node = null
              meta.status = null
              meta.done = true
              break
            default:
              continue // unknown event type: ignore, don't re-render
          }
          yield snapshot()
        }
      } catch (err) {
        if (abortSignal.aborted) return
        throw err
      }
      if (!meta.done) throw new Error('bdc-assist closed the stream before finishing')
    },
  }
}
