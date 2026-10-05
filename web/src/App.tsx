import { AssistantRuntimeProvider, useLocalRuntime } from '@assistant-ui/react'

import { Thread } from '@/components/assistant-ui/elements/thread.aui'
import { BdcMessageParts } from '@/components/bdc/message-parts'
import { TooltipProvider } from '@/components/ui/tooltip'
import { createBdcAdapter } from '@/lib/bdc-adapter'

// demo_services.sh runs bdc-assist on :8010; the stub server is on :8011
const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8010'
const adapter = createBdcAdapter(API_URL)

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
