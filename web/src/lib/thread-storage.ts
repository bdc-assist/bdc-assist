import type { ExportedMessageRepositoryItem, ThreadHistoryAdapter } from '@assistant-ui/react'

// Keeps the one current conversation in the browser, so a reload doesn't lose
// it. The runtime appends each message once it settles (complete, stopped or
// failed) and loads them all back on start. Message metadata is plain JSON by
// design (see BdcMessageMeta); tool_results never reach the client at all.

const KEY = 'bdc-assist:thread:v1'

type Stored = { headId: string | null; messages: ExportedMessageRepositoryItem[] }

const empty = (): Stored => ({ headId: null, messages: [] })

export type ThreadStorage = ThreadHistoryAdapter & {
  /** Forget the stored conversation (the runtime's reset() doesn't tell us). */
  clear(): void
}

/** The browser's localStorage, or null where it's unavailable (merely reading
 * it throws when site data is blocked). */
export function browserStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

export function createThreadStorage(storage: Storage | null): ThreadStorage {
  // storage can be missing or throw (private mode, quota, blocked site data):
  // then the conversation just isn't kept
  const read = (): Stored => {
    try {
      const raw = storage?.getItem(KEY)
      if (!raw) return empty()
      const stored = JSON.parse(raw) as Stored
      for (const item of stored.messages) {
        // JSON turned the Date into a string
        ;(item.message as { createdAt: Date }).createdAt = new Date(item.message.createdAt)
      }
      return stored
    } catch {
      return empty()
    }
  }
  const write = (stored: Stored) => {
    try {
      storage?.setItem(KEY, JSON.stringify(stored))
    } catch {
      // keep going without persistence
    }
  }
  const upsert = (item: ExportedMessageRepositoryItem, makeHead: boolean) => {
    const stored = read()
    // a message whose parent isn't kept belongs to a conversation that was
    // cleared mid-answer (the stopped run settles after the clear): drop it
    if (item.parentId !== null && !stored.messages.some((m) => m.message.id === item.parentId)) return
    const messages = stored.messages.filter((m) => m.message.id !== item.message.id)
    messages.push(item)
    write({ headId: makeHead ? item.message.id : stored.headId, messages })
  }

  return {
    async load() {
      return read()
    },
    async append(item) {
      upsert(item, true) // the newest message is the end of the branch on screen
    },
    async update(item) {
      upsert(item, false)
    },
    clear() {
      try {
        storage?.removeItem(KEY)
      } catch {
        // nothing stored to forget
      }
    },
  }
}
