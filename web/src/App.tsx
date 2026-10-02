import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ChatModelAdapter,
} from '@assistant-ui/react'

import { Thread } from '@/components/assistant-ui/elements/thread.aui'
import { TooltipProvider } from '@/components/ui/tooltip'

// Placeholder until the bdc-assist adapter lands: streams a canned markdown
// reply word by word, so the UI can be exercised with no API running.
const dummyAdapter: ChatModelAdapter = {
  async *run({ messages, abortSignal }) {
    const last = messages.at(-1)
    const question = last?.content.find((p) => p.type === 'text')?.text ?? ''
    const reply =
      `You asked: **${question}**\n\n` +
      'This is a *dummy* reply, streamed one word at a time.\n\n' +
      '- markdown lists\n- [links](https://biodatacatalyst.nhlbi.nih.gov)\n- `inline code`'
    let text = ''
    for (const word of reply.split(/(?<= )/)) {
      if (abortSignal.aborted) return
      await new Promise((r) => setTimeout(r, 40))
      text += word
      yield { content: [{ type: 'text', text }] }
    }
  },
}

export default function App() {
  const runtime = useLocalRuntime(dummyAdapter)
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <TooltipProvider>
        <div className="h-dvh">
          <Thread />
        </div>
      </TooltipProvider>
    </AssistantRuntimeProvider>
  )
}
