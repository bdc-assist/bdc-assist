import { makeAssistantDataUI } from '@assistant-ui/react'
import { FileTextIcon, LoaderCircleIcon, ShieldAlertIcon } from 'lucide-react'

import {
  BLOCKED_PART,
  SOURCES_PART,
  STATUS_PART,
  type Source,
  type Sources,
  type StatusPartData,
} from '@/lib/bdc-adapter'

// Renderers for the `data` parts bdc-adapter appends after the answer text.
// Each makeAssistantDataUI component registers its renderer while mounted.

const NODE_LABELS: Record<string, string> = {
  input_guardrail: 'Screening the question…',
  contextualize: 'Reading the conversation…',
  classify: 'Checking known topics…',
  agent: 'Thinking…',
  output_guardrail: 'Reviewing the answer…',
  output_reject: 'Answer rejected…',
  append_disclaimer: 'Adding disclaimers…',
  suggest_followups: 'Suggesting follow-ups…',
}

const toolLabel = (status: string) =>
  status.replace(/^calling (\w+)$/, (_, tool: string) => `Using ${tool.replace(/_/g, ' ')}…`)

const StatusUI = makeAssistantDataUI<StatusPartData>({
  name: STATUS_PART,
  render: ({ data: { node, status } }) => (
    <p data-slot="bdc-status" className="text-muted-foreground my-2 flex items-center gap-1.5 text-sm">
      <LoaderCircleIcon className="size-3.5 animate-spin motion-reduce:animate-none" />
      {status ? toolLabel(status) : (NODE_LABELS[node!] ?? `${node}…`)}
    </p>
  ),
})

const SOURCE_TYPES: Record<string, string> = { page: 'Page', faq: 'FAQ' }

const SourcesUI = makeAssistantDataUI<Sources>({
  name: SOURCES_PART,
  render: ({ data }) => {
    const sources: Source[] = Object.values(data as Sources).flat()
    return (
      <div className="border-border mt-4 border-t pt-3">
        <p className="text-muted-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
          Sources
        </p>
        <ul className="flex flex-col gap-1">
          {sources.map((s) => (
            <li key={s.link} className="flex items-baseline gap-2 text-sm">
              <FileTextIcon className="text-muted-foreground size-3.5 shrink-0 translate-y-0.5" />
              <a
                href={s.link}
                target="_blank"
                rel="noreferrer"
                className="text-primary hover:text-primary/80 min-w-0 truncate underline underline-offset-2"
              >
                {s.title}
              </a>
              {s.type && (
                <span className="text-muted-foreground shrink-0 text-xs">
                  {SOURCE_TYPES[s.type] ?? s.type}
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>
    )
  },
})

const BlockedUI = makeAssistantDataUI({
  name: BLOCKED_PART,
  render: () => (
    <p className="text-muted-foreground mt-3 flex items-center gap-1.5 text-xs">
      <ShieldAlertIcon className="size-3.5" />
      Outside what BDC Assist can help with
    </p>
  ),
})

/** Mount once inside the runtime provider. */
export function BdcMessageParts() {
  return (
    <>
      <StatusUI />
      <SourcesUI />
      <BlockedUI />
    </>
  )
}
