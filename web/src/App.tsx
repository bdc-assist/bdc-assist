import { AssistantRuntimeProvider, useLocalRuntime } from '@assistant-ui/react'

import { Thread } from '@/components/assistant-ui/elements/thread.aui'
import { BdcMessageParts } from '@/components/bdc/message-parts'
import { TooltipProvider } from '@/components/ui/tooltip'
import { createBdcAdapter, type Reveal } from '@/lib/bdc-adapter'

// demo_services.sh runs bdc-assist on :8010; the stub server is on :8011
const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8010'
// ?reveal=after-check (or VITE_REVEAL) holds the answer back until it's checked
const REVEAL: Reveal =
  (new URLSearchParams(location.search).get('reveal') ?? import.meta.env.VITE_REVEAL) ===
  'after-check'
    ? 'after-check'
    : 'stream'
const adapter = createBdcAdapter(API_URL, { reveal: REVEAL })

export default function App() {
  const runtime = useLocalRuntime(adapter)
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <BdcMessageParts />
      <TooltipProvider>
        <div className="h-dvh">
          <Thread />
        </div>
      </TooltipProvider>
    </AssistantRuntimeProvider>
  )
}
