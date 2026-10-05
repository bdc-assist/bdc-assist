import type { ChatModelRunOptions, ChatModelRunResult, ThreadMessage } from '@assistant-ui/react'
import { describe, expect, it, vi } from 'vitest'

import { createBdcAdapter, toRequest, type BdcMessageMeta } from '@/lib/bdc-adapter'
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
async function run(chunks: string[], opts: { status?: number; signal?: AbortSignal } = {}) {
  const fetchImpl = vi.fn(async () => new Response(streamOf(chunks), { status: opts.status ?? 200 }))
  const adapter = createBdcAdapter('http://api', fetchImpl as unknown as typeof fetch)
  const yields: ChatModelRunResult[] = []
  const gen = adapter.run({
    messages: [msg('user', 'What is BDC?')],
    abortSignal: opts.signal ?? new AbortController().signal,
  } as unknown as ChatModelRunOptions) as AsyncGenerator<ChatModelRunResult>
  for await (const y of gen) yields.push(y)
  return { yields, fetchImpl }
}

const textOf = (y: ChatModelRunResult) => (y.content?.[0] as { text: string }).text
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
    expect(metaOf(yields[2]).status).toBeNull() // tokens clear the status
    expect(metaOf(yields.at(-1)!)).toEqual({
      node: null,
      status: null,
      sources: { 'bdc-doc': [{ title: 'Overview', link: 'https://x/overview', type: 'page' }] },
      followups: ['What is dbGaP?'],
      blocked: false,
      done: true,
    })
  })

  it('discards streamed text on reset', async () => {
    const { yields } = await run(
      sse({ type: 'token', text: 'Let me look that up.' }, { type: 'reset' }, { type: 'token', text: 'BDC' }, done()),
    )
    expect(yields.map(textOf).slice(0, 3)).toEqual(['Let me look that up.', '', 'BDC'])
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
    await expect(run(sse({ type: 'token', text: 'half' }))).rejects.toThrow('before finishing')
  })

  it('returns quietly when the user cancels', async () => {
    const ctl = new AbortController()
    const fetchImpl = vi.fn(async () => {
      ctl.abort()
      throw new DOMException('aborted', 'AbortError')
    })
    const adapter = createBdcAdapter('http://api', fetchImpl as unknown as typeof fetch)
    const gen = adapter.run({
      messages: [msg('user', 'q')],
      abortSignal: ctl.signal,
    } as unknown as ChatModelRunOptions) as AsyncGenerator<ChatModelRunResult>
    const yields = []
    for await (const y of gen) yields.push(y)
    expect(yields).toEqual([])
  })
})
