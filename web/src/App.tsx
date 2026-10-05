import {
  AssistantRuntimeProvider,
  AuiConfig,
  AuiProvider,
  Suggestions,
  useAui,
  useLocalRuntime,
} from '@assistant-ui/react'
import type { ReactNode } from 'react'

import { Thread } from '@/components/assistant-ui/elements/thread.aui'
import { BdcMessageParts } from '@/components/bdc/message-parts'
import { TooltipProvider } from '@/components/ui/tooltip'
import { bdcSuggestionAdapter, createBdcAdapter, type Reveal } from '@/lib/bdc-adapter'

// demo_services.sh runs bdc-assist on :8010; the stub server is on :8011
const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8010'
// ?reveal=after-check (or VITE_REVEAL) holds the answer back until it's checked
const REVEAL: Reveal =
  (new URLSearchParams(location.search).get('reveal') ?? import.meta.env.VITE_REVEAL) ===
  'after-check'
    ? 'after-check'
    : 'stream'
const adapter = createBdcAdapter(API_URL, { reveal: REVEAL })

// shown on the empty thread; clicking one sends it
const STARTERS = [
  'What is PIC-SURE and what can I do with it in BDC?',
  'How do I upload my own data to BDC?',
  'Does BDC have COVID data?',
  'What are the latest BDC events?',
]

function Starters({ children }: { children: ReactNode }) {
  const aui = useAui()
  return (
    <AuiProvider extends={aui} config={AuiConfig({ suggestions: Suggestions(STARTERS) })}>
      {children}
    </AuiProvider>
  )
}

export default function App() {
  const runtime = useLocalRuntime(adapter, { adapters: { suggestion: bdcSuggestionAdapter } })
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <BdcMessageParts />
      <Starters>
        <TooltipProvider>
          <div className="h-dvh">
            <Thread />
          </div>
        </TooltipProvider>
      </Starters>
    </AssistantRuntimeProvider>
  )
}
