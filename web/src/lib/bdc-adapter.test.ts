import type { ChatModelRunOptions, ChatModelRunResult, ThreadMessage } from '@assistant-ui/react'
import { describe, expect, it, vi } from 'vitest'

import {
  BLOCKED_PART,
  createBdcAdapter,
  DRAFT_PART,
  FOLLOWUPS_PART,
  GRAPH_PART,
  REJECTED_PART,
  SOURCES_PART,
  STATUS_PART,
  toRequest,
  type BdcMessageMeta,
  type Reveal,
} from '@/lib/bdc-adapter'
import { streamOf } from '@/lib/test-utils'

const msg = (role: 'user' | 'assistant', text: string) =>
  ({ role, content: [{ type: 'text', text }] }) as unknown as ThreadMessage

const sse = (...events: object[]) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`)

const done = (over: object = {}) => ({
  type: 'done',
  answer: 'Final answer.',
  blocked: false,
  topics: [],
  followups: ['What is dbGaP?'],
  tool_results: [{ tool: 'search_docs' }],
  sources: { 'bdc-doc': [{ title: 'Overview', link: 'https://x/overview', type: 'page' }] },
  sources_md: '- [Overview](https://x/overview)',
  ...over,
})

/** Runs the adapter against a canned response; returns every yield. */
async function run(
  chunks: string[],
  opts: { status?: number; signal?: AbortSignal; reveal?: Reveal } = {},
) {
  const fetchImpl = vi.fn(async () => new Response(streamOf(chunks), { status: opts.status ?? 200 }))
  const adapter = createBdcAdapter('http://api', {
    fetchImpl: fetchImpl as unknown as typeof fetch,
    reveal: opts.reveal,
  })
  const yields: ChatModelRunResult[] = []
  const gen = adapter.run({
    messages: [msg('user', 'What is BDC?')],
    abortSignal: opts.signal ?? new AbortController().signal,
  } as unknown as ChatModelRunOptions) as AsyncGenerator<ChatModelRunResult>
  for await (const y of gen) yields.push(y)
  return { yields, fetchImpl }
}

const textOf = (y: ChatModelRunResult) => (y.content![0] as { text: string }).text
const metaOf = (y: ChatModelRunResult) => y.metadata?.custom as BdcMessageMeta

describe('toRequest', () => {
  it('sends the last user message as input and earlier turns as history', () => {
    const req = toRequest([msg('user', 'q1'), msg('assistant', 'a1'), msg('user', 'q2')])
    expect(req).toEqual({
      input: 'q2',
      chat_history: [
        { role: 'user', content: 'q1' },
        { role: 'assistant', content: 'a1' },
      ],
    })
  })

  it('drops empty turns (e.g. a cancelled reply)', () => {
    const req = toRequest([msg('user', 'q1'), msg('assistant', ''), msg('user', 'q2')])
    expect(req.chat_history).toEqual([{ role: 'user', content: 'q1' }])
  })
})

describe('createBdcAdapter', () => {
  it('POSTs to /chat/stream', async () => {
    const { fetchImpl } = await run(sse(done()))
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api/chat/stream')
    expect(JSON.parse(init.body as string)).toEqual({ input: 'What is BDC?', chat_history: [] })
  })

  it('streams tokens, tracks node and status, then finishes on done', async () => {
    const { yields } = await run(
      sse(
        { type: 'node', node: 'agent' },
        { type: 'status', text: 'calling search_docs' },
        { type: 'token', text: 'BDC ' },
        { type: 'token', text: 'is' },
        done({ answer: 'BDC is.' }),
      ),
    )
    expect(yields.map(textOf)).toEqual(['', '', 'BDC ', 'BDC is', 'BDC is.'])
    expect(metaOf(yields[0])).toMatchObject({ node: 'agent', status: null })
    expect(metaOf(yields[1]).status).toBe('calling search_docs')
    expect(metaOf(yields[2]).status).toBe('calling search_docs') // tokens don't clear it
    expect(metaOf(yields.at(-1)!)).toEqual({
      node: null,
      status: null,
      sources: { 'bdc-doc': [{ title: 'Overview', link: 'https://x/overview', type: 'page' }] },
      graph: null,
      followups: ['What is dbGaP?'],
      blocked: false,
      rejected: false,
      done: true,
    })
  })

  it('discards streamed text and the tool status on reset', async () => {
    const { yields } = await run(
      sse(
        { type: 'status', text: 'calling search_docs' },
        { type: 'token', text: 'Let me look that up.' },
        { type: 'reset' },
        { type: 'token', text: 'BDC' },
        done(),
      ),
    )
    expect(yields.map(textOf).slice(1, 4)).toEqual(['Let me look that up.', '', 'BDC'])
    expect(metaOf(yields[1]).status).toBe('calling search_docs')
    expect(metaOf(yields[2]).status).toBeNull()
  })

  it('shows sources as soon as the agent reports them', async () => {
    const sources = { 'bdc-doc': [{ title: 'A', link: 'https://a', type: 'faq' }] }
    const { yields } = await run(sse({ type: 'sources', sources, sources_md: '- A' }, done()))
    expect(metaOf(yields[0])).toMatchObject({ sources, done: false })
  })

  it('lets done override streamed text and sources (rejected answer)', async () => {
    const { yields } = await run(
      sse(
        { type: 'token', text: 'a dubious answer' },
        { type: 'sources', sources: { 'bdc-doc': [{ title: 'A', link: 'https://a', type: 'faq' }] }, sources_md: '- A' },
        done({ answer: 'Sorry, I can only help with BDC.', sources: {}, sources_md: '', followups: [] }),
      ),
    )
    const last = yields.at(-1)!
    expect(textOf(last)).toBe('Sorry, I can only help with BDC.')
    expect(metaOf(last).sources).toEqual({})
    expect(metaOf(last).rejected).toBe(true)
  })

  it('keeps the graph from its event, and lets done override it', async () => {
    const graph = {
      nodes: [
        { id: 'MONDO:1', label: 'chd', type: 'concept', concept_type: 'biolink.NamedThing' },
        { id: 'phv1', label: 'FC219', type: 'variable', related_concepts_count: 12 },
      ],
      edges: [{ source: 'phv1', target: 'MONDO:1' }],
    }
    const kept = await run(sse({ type: 'graph', graph }, done({ graph })))
    expect(metaOf(kept.yields[0])).toMatchObject({ graph, done: false })
    expect(metaOf(kept.yields.at(-1)!).graph).toEqual(graph)
    // rejected answer: done sends {} — the streamed graph goes too
    const rejected = await run(sse({ type: 'graph', graph }, done({ graph: {} })))
    expect(metaOf(rejected.yields.at(-1)!).graph).toBeNull()
  })

  it('has no graph without one (older servers send no graph field)', async () => {
    const { yields } = await run(sse(done()))
    expect(metaOf(yields[0]).graph).toBeNull()
  })

  it('marks blocked replies', async () => {
    const { yields } = await run(sse(done({ blocked: true })))
    expect(metaOf(yields[0]).blocked).toBe(true)
  })

  it('never keeps tool_results', async () => {
    const { yields } = await run(sse(done()))
    expect(JSON.stringify(yields)).not.toContain('tool_results')
  })

  it('ignores unknown event types', async () => {
    const { yields } = await run(sse({ type: 'mystery' }, done()))
    expect(yields).toHaveLength(1)
  })

  it('throws on an HTTP error', async () => {
    await expect(run([], { status: 500 })).rejects.toThrow('HTTP 500')
  })

  it('throws if the stream ends without done', async () => {
    await expect(run(sse({ type: 'token', text: 'half' }))).rejects.toThrow('stopped before finishing')
  })

  it('returns quietly when the user cancels', async () => {
    const ctl = new AbortController()
    const fetchImpl = vi.fn(async () => {
      ctl.abort()
      throw new DOMException('aborted', 'AbortError')
    })
    const adapter = createBdcAdapter('http://api', { fetchImpl: fetchImpl as unknown as typeof fetch })
    const gen = adapter.run({
      messages: [msg('user', 'q')],
      abortSignal: ctl.signal,
    } as unknown as ChatModelRunOptions) as AsyncGenerator<ChatModelRunResult>
    const yields = []
    for await (const y of gen) yields.push(y)
    expect(yields).toEqual([])
  })
})

describe('data parts', () => {
  const partsOf = (y: ChatModelRunResult) =>
    (y.content ?? []).slice(1).map((p) => (p as { name: string; data: unknown }))
  const names = (y: ChatModelRunResult) => partsOf(y).map((p) => p.name)

  it('keeps the text part first', async () => {
    const { yields } = await run(sse({ type: 'node', node: 'agent' }, done({ blocked: true })))
    for (const y of yields) expect(y.content?.[0].type).toBe('text')
  })

  it('shows the node label until tokens flow, and the tool status until reset', async () => {
    const { yields } = await run(
      sse(
        { type: 'node', node: 'agent' },
        { type: 'status', text: 'calling search_docs' },
        { type: 'token', text: 'Let me look that up.' },
        { type: 'reset' },
        { type: 'token', text: 'BDC' },
        { type: 'node', node: 'output_guardrail' },
        done(),
      ),
    )
    const statusOf = (y: ChatModelRunResult) => partsOf(y).find((p) => p.name === STATUS_PART)?.data
    expect(partsOf(yields[0])).toEqual([{ type: 'data', name: STATUS_PART, data: { node: 'agent', status: null } }])
    expect(statusOf(yields[1])).toEqual({ node: 'agent', status: 'calling search_docs' })
    expect(statusOf(yields[2])).toEqual({ node: 'agent', status: 'calling search_docs' }) // tool still running
    expect(statusOf(yields[3])).toEqual({ node: 'agent', status: null }) // reset: "Thinking…"
    expect(names(yields[4])).toEqual([]) // answer streaming
    expect(statusOf(yields[5])).toEqual({ node: 'output_guardrail', status: null })
    expect(names(yields[6])).not.toContain(STATUS_PART) // done
  })

  it('adds sources once known, and none for empty sources', async () => {
    const { yields } = await run(sse(done({ followups: [] })))
    expect(names(yields[0])).toEqual([SOURCES_PART])
    const empty = await run(sse(done({ sources: {}, followups: [] })))
    expect(names(empty.yields[0])).toEqual([])
    const emptyList = await run(sse(done({ sources: { 'bdc-doc': [] }, followups: [] })))
    expect(names(emptyList.yields[0])).toEqual([])
  })

  it('adds the graph once known, before the status line, and holds it back in after-check', async () => {
    const graph = { nodes: [{ id: 'C', label: 'c', type: 'concept' }], edges: [] }
    const events = sse({ type: 'graph', graph }, { type: 'node', node: 'output_guardrail' }, done({ graph }))
    const stream = await run(events)
    expect(names(stream.yields[0])).toContain(GRAPH_PART)
    const running = names(stream.yields[1])
    expect(running.indexOf(GRAPH_PART)).toBeLessThan(running.indexOf(STATUS_PART))
    const held = await run(events, { reveal: 'after-check' })
    expect(names(held.yields[1])).not.toContain(GRAPH_PART)
    expect(names(held.yields.at(-1)!)).toContain(GRAPH_PART)
  })

  it('adds the follow-ups last, once done', async () => {
    const { yields } = await run(sse({ type: 'node', node: 'agent' }, done({ followups: ['A?', 'B?'] })))
    expect(names(yields[0])).not.toContain(FOLLOWUPS_PART)
    const last = partsOf(yields.at(-1)!)
    expect(last.at(-1)).toEqual({ type: 'data', name: FOLLOWUPS_PART, data: ['A?', 'B?'] })
  })

  it('adds no follow-ups when there are none, or for a blocked question', async () => {
    expect(names((await run(sse(done({ followups: [] })))).yields[0])).not.toContain(FOLLOWUPS_PART)
    expect(names((await run(sse(done({ blocked: true })))).yields[0])).not.toContain(FOLLOWUPS_PART)
  })

  it('adds the blocked notice', async () => {
    const { yields } = await run(sse(done({ blocked: true, sources: {} })))
    expect(names(yields[0])).toEqual([BLOCKED_PART])
  })

  it('never sends data parts as history', () => {
    const assistant = {
      role: 'assistant',
      content: [
        { type: 'text', text: 'a1' },
        { type: 'data', name: SOURCES_PART, data: {} },
      ],
    } as unknown as ThreadMessage
    expect(toRequest([msg('user', 'q1'), assistant, msg('user', 'q2')]).chat_history[1]).toEqual({
      role: 'assistant',
      content: 'a1',
    })
  })
})

describe('rejection', () => {
  const finalMeta = async (...events: object[]) => metaOf((await run(sse(...events))).yields.at(-1)!)

  it('is inferred when done replaces the streamed draft', async () => {
    const meta = await finalMeta({ type: 'token', text: 'Off  target\n draft.' }, done({ answer: 'Canned.' }))
    expect(meta.rejected).toBe(true)
  })

  it('is not inferred for a passed answer with a disclaimer appended', async () => {
    const meta = await finalMeta(
      { type: 'token', text: 'BDC is ' },
      { type: 'token', text: 'a platform. ' },
      done({ answer: 'BDC is a platform.\n\nCovid disclaimer.' }),
    )
    expect(meta.rejected).toBe(false)
  })

  it('is not inferred without a draft, or for a blocked question', async () => {
    expect((await finalMeta(done({ answer: 'Canned.' }))).rejected).toBe(false)
    expect((await finalMeta({ type: 'token', text: 'x' }, done({ answer: 'Refusal.', blocked: true }))).rejected).toBe(false)
  })

  it('adds a note in stream mode only', async () => {
    const events = sse({ type: 'token', text: 'draft' }, done({ answer: 'Canned.' }))
    const names = (y: ChatModelRunResult) => (y.content ?? []).map((p) => (p as { name?: string }).name)
    expect(names((await run(events)).yields.at(-1)!)).toContain(REJECTED_PART)
    expect(names((await run(events, { reveal: 'after-check' })).yields.at(-1)!)).not.toContain(REJECTED_PART)
  })
})

describe("reveal: 'after-check'", () => {
  const events = sse(
    { type: 'node', node: 'agent' },
    { type: 'token', text: 'one two three ' },
    { type: 'sources', sources: { 'bdc-doc': [{ title: 'A', link: 'https://a', type: 'faq' }] }, sources_md: '- A' },
    { type: 'token', text: 'four' },
    done({ answer: 'one two three four', followups: [] }),
  )
  const partsOf = (y: ChatModelRunResult) =>
    (y.content ?? []).slice(1).map((p) => p as { name: string; data: unknown })

  it('holds the text back and shows a growing draft instead', async () => {
    const { yields } = await run(events, { reveal: 'after-check' })
    expect(yields.slice(0, 4).map(textOf)).toEqual(['', '', '', ''])
    expect(partsOf(yields[1]).find((p) => p.name === DRAFT_PART)?.data).toEqual({ words: 3 })
    expect(partsOf(yields[3]).find((p) => p.name === DRAFT_PART)?.data).toEqual({ words: 4 })
    expect(textOf(yields[4])).toBe('one two three four')
    expect(partsOf(yields[4]).map((p) => p.name)).toEqual([SOURCES_PART])
  })

  it('keeps the status while tokens flow, and holds sources until done', async () => {
    const { yields } = await run(events, { reveal: 'after-check' })
    expect(partsOf(yields[1]).map((p) => p.name)).toEqual([DRAFT_PART, STATUS_PART])
    expect(partsOf(yields[2]).map((p) => p.name)).not.toContain(SOURCES_PART)
  })

  it('stream mode shows the draft text instead, with sources as they come', async () => {
    const { yields } = await run(events)
    expect(textOf(yields[1])).toBe('one two three ')
    expect(partsOf(yields[1]).map((p) => p.name)).toEqual([])
    expect(partsOf(yields[2]).map((p) => p.name)).toEqual([SOURCES_PART])
    expect(partsOf(yields[4]).map((p) => p.name)).toEqual([SOURCES_PART])
  })
})

describe('errors', () => {
  it('explains an unreachable API', async () => {
    const adapter = createBdcAdapter('http://api', {
      fetchImpl: (async () => {
        throw new TypeError('fetch failed')
      }) as unknown as typeof fetch,
    })
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const gen = adapter.run({
      messages: [msg('user', 'q')],
      abortSignal: new AbortController().signal,
    } as unknown as ChatModelRunOptions) as AsyncGenerator<ChatModelRunResult>
    await expect(gen.next()).rejects.toThrow("Can't reach BDC Assist at http://api. Is the API running?")
  })
})
