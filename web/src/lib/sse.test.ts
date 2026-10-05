import { describe, expect, it } from 'vitest'

import { readSSE } from '@/lib/sse'
import { streamOf } from '@/lib/test-utils'

const collect = async (chunks: string[]) => {
  const out = []
  for await (const ev of readSSE(streamOf(chunks))) out.push(ev)
  return out
}

describe('readSSE', () => {
  it('parses one event per blank-line-terminated block', async () => {
    expect(await collect(['data: {"a":1}\n\ndata: {"b":2}\n\n'])).toEqual([{ a: 1 }, { b: 2 }])
  })

  it('reassembles events split across network chunks', async () => {
    expect(await collect(['data: {"te', 'xt":"hi"}\n', '\ndata: 2\n\n'])).toEqual([{ text: 'hi' }, 2])
  })

  it('keeps a multibyte character split across chunks', async () => {
    const bytes = new TextEncoder().encode('data: "é"\n\n')
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, 8)) // cuts é (2 bytes) in half
        c.enqueue(bytes.slice(8))
        c.close()
      },
    })
    const out = []
    for await (const ev of readSSE(body)) out.push(ev)
    expect(out).toEqual(['é'])
  })

  it('handles CRLF, comments and a missing final blank line', async () => {
    expect(await collect([': ping\r\n\r\ndata: 1\r\n\r\ndata: 2'])).toEqual([1, 2])
  })
})
