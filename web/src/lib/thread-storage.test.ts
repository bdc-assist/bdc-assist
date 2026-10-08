import type { ExportedMessageRepositoryItem, ThreadMessage } from '@assistant-ui/react'
import { describe, expect, it } from 'vitest'

import { createThreadStorage } from '@/lib/thread-storage'

/** Just enough of the Storage interface, backed by a Map. */
function memoryStorage() {
  const map = new Map<string, string>()
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
  } as unknown as Storage
}

const item = (id: string, parentId: string | null, text = id): ExportedMessageRepositoryItem => ({
  parentId,
  message: {
    id,
    role: 'user',
    createdAt: new Date('2026-10-05T12:00:00Z'),
    content: [{ type: 'text', text }],
    metadata: { custom: {} },
  } as unknown as ThreadMessage,
})

describe('createThreadStorage', () => {
  it('starts empty', async () => {
    expect(await createThreadStorage(memoryStorage()).load()).toEqual({ headId: null, messages: [] })
  })

  it('appends messages, newest as head, and survives a new instance', async () => {
    const storage = memoryStorage()
    const a = createThreadStorage(storage)
    await a.append(item('u1', null))
    await a.append(item('a1', 'u1'))
    const loaded = await createThreadStorage(storage).load()
    expect(loaded.headId).toBe('a1')
    expect(loaded.messages.map((m) => m.message.id)).toEqual(['u1', 'a1'])
    expect(loaded.messages[0].message.createdAt).toEqual(new Date('2026-10-05T12:00:00Z'))
  })

  it('update replaces a message in place without moving the head', async () => {
    const s = createThreadStorage(memoryStorage())
    await s.append(item('u1', null))
    await s.append(item('a1', 'u1'))
    await s.update!(item('u1', null, 'edited'))
    const loaded = await s.load()
    expect(loaded.headId).toBe('a1')
    expect(loaded.messages).toHaveLength(2)
    expect(loaded.messages.find((m) => m.message.id === 'u1')!.message.content).toEqual([
      { type: 'text', text: 'edited' },
    ])
  })

  it('drops a message that settles after its conversation was cleared', async () => {
    const s = createThreadStorage(memoryStorage())
    await s.append(item('u1', null))
    s.clear()
    await s.append(item('a1', 'u1')) // the stopped answer to u1, arriving late
    expect((await s.load()).messages).toEqual([])
  })

  it('clear forgets everything', async () => {
    const s = createThreadStorage(memoryStorage())
    await s.append(item('u1', null))
    s.clear()
    expect((await s.load()).messages).toEqual([])
  })

  it('works, without keeping anything, when storage is missing or broken', async () => {
    const broken = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('quota')
      },
      removeItem: () => {
        throw new Error('blocked')
      },
    } as unknown as Storage
    for (const s of [createThreadStorage(null), createThreadStorage(broken)]) {
      await s.append(item('u1', null))
      s.clear()
      expect(await s.load()).toEqual({ headId: null, messages: [] })
    }
  })

  it('ignores corrupt stored data', async () => {
    const storage = memoryStorage()
    storage.setItem('bdc-assist:thread:v1', '{not json')
    expect((await createThreadStorage(storage).load()).messages).toEqual([])
  })
})
