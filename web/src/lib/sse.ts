// Minimal server-sent-events reader for a fetch() body. EventSource can't be
// used: it only does GET, and /chat/stream is a POST.

/** Yields the JSON payload of each `data:` event, in order. */
export async function* readSSE(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<unknown> {
  const reader = body.getReader()
  const decoder = new TextDecoder() // stream mode keeps multibyte chars split across reads
  let buf = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      // an event ends at a blank line; the tail may be half an event
      const events = buf.split('\n\n')
      buf = events.pop()!
      for (const ev of events) {
        const data = parseEvent(ev)
        if (data !== undefined) yield data
      }
    }
    const data = parseEvent(buf) // stream closed without a trailing blank line
    if (data !== undefined) yield data
  } finally {
    reader.releaseLock()
  }
}

function parseEvent(raw: string): unknown {
  const lines = raw
    .split('\n')
    .filter((l) => l.startsWith('data:'))
    .map((l) => l.slice(5).replace(/^ /, ''))
  return lines.length ? JSON.parse(lines.join('\n')) : undefined
}
