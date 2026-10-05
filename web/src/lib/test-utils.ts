/** A fetch-like body that delivers `chunks` as separate network reads. */
export function streamOf(chunks: string[]) {
  const enc = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(c) {
      for (const s of chunks) c.enqueue(enc.encode(s))
      c.close()
    },
  })
}
