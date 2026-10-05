import { makeAssistantDataUI, useAuiState } from '@assistant-ui/react'
import {
  BookOpenIcon,
  CalendarDaysIcon,
  CircleHelpIcon,
  CirclePlayIcon,
  FileTextIcon,
  GlobeIcon,
  LoaderCircleIcon,
  NewspaperIcon,
  RotateCcwIcon,
  ShieldAlertIcon,
  UserRoundIcon,
  type LucideIcon,
} from 'lucide-react'

import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

import {
  BLOCKED_PART,
  DRAFT_PART,
  REJECTED_PART,
  SOURCES_PART,
  STATUS_PART,
  type DraftPartData,
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

// The last snapshot of a stopped or failed answer still holds its progress
// parts; draw them only while the answer is running, so no spinner is left behind.
const useRunning = () => useAuiState((s) => s.message.status?.type === 'running')

const StatusUI = makeAssistantDataUI<StatusPartData>({
  name: STATUS_PART,
  render: function StatusLine({ data: { node, status } }) {
    if (!useRunning()) return null
    return (
      <p data-slot="bdc-status" className="text-muted-foreground my-2 flex items-center gap-1.5 text-sm">
        <LoaderCircleIcon className="size-3.5 animate-spin motion-reduce:animate-none" />
        {status ? toolLabel(status) : (NODE_LABELS[node!] ?? `${node}…`)}
      </p>
    )
  },
})

// doc_type values come from bdc-doc-mcp's ingest (SOURCE_DOC_TYPES)
const SOURCE_TYPES: Record<string, { label: string; Icon: LucideIcon }> = {
  page: { label: 'BDC website', Icon: GlobeIcon },
  docs: { label: 'Documentation', Icon: BookOpenIcon },
  faq: { label: 'FAQ', Icon: CircleHelpIcon },
  video: { label: 'Video', Icon: CirclePlayIcon },
  event: { label: 'Event', Icon: CalendarDaysIcon },
  update: { label: 'Update', Icon: NewspaperIcon },
  fellow: { label: 'Fellow', Icon: UserRoundIcon },
}
const OTHER_SOURCE = { label: 'Source', Icon: FileTextIcon }

// Right-aligned on the line of the message's copy/refresh toolbar. That
// toolbar is vendored (thread.aui.tsx AssistantMessage), so rather than edit
// it, this row copies its box: the message root is `relative pb-7.5`, and the
// footer above that padding is `min-h-7.5 pt-1.5` with 24px (size-6) buttons.
// It must stay inside the root's box: the root uses content-visibility, which
// clips anything painted outside it.
// Shown and hidden with the toolbar it sits beside, by the same rules as its
// ActionBarPrimitive.Root (hideWhenRunning, autohide="not-last").
const useToolbarVisible = () =>
  useAuiState((s) => !s.thread.isRunning && (s.message.isLast || s.message.isHovering))

const SourcesUI = makeAssistantDataUI<Sources>({
  name: SOURCES_PART,
  render: function SourcesRow({ data }) {
    const visible = useToolbarVisible()
    if (!visible) return null
    const sources: Source[] = Object.values(data as Sources).flat()
    return (
      <ul
        data-slot="bdc-sources"
        aria-label="Sources"
        className="absolute right-2 bottom-7.5 flex min-h-7.5 items-center gap-1 pt-1.5"
      >
        {sources.map((s) => {
          const { label, Icon } = SOURCE_TYPES[s.type] ?? OTHER_SOURCE
          return (
            <li key={s.link}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <a
                    href={s.link}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`${s.title} (${label})`}
                    className="text-muted-foreground hover:text-foreground hover:bg-muted flex size-6 items-center justify-center rounded-md p-1 transition-colors"
                  >
                    <Icon className="size-4" />
                  </a>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="flex-col items-start gap-0">
                  <span className="font-medium">{s.title}</span>
                  <span className="opacity-70">{label}</span>
                </TooltipContent>
              </Tooltip>
            </li>
          )
        })}
      </ul>
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

const WORDS_PER_BAR = 12
const MAX_BARS = 10

const DraftUI = makeAssistantDataUI<DraftPartData>({
  name: DRAFT_PART,
  render: function DraftBars({ data: { words } }) {
    if (!useRunning()) return null
    const bars = Math.min(MAX_BARS, Math.ceil(words / WORDS_PER_BAR))
    return (
      <div className="my-1 flex flex-col gap-2" aria-label="Writing the answer">
        {Array.from({ length: bars }, (_, i) => (
          <Skeleton key={i} className={i === bars - 1 ? 'h-4 w-3/5' : 'h-4 w-full'} />
        ))}
      </div>
    )
  },
})

const RejectedUI = makeAssistantDataUI({
  name: REJECTED_PART,
  render: () => (
    <p className="text-muted-foreground mt-3 flex items-center gap-1.5 text-xs">
      <RotateCcwIcon className="size-3.5" />
      Replaces a first draft that didn't answer the question
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
      <DraftUI />
      <RejectedUI />
    </>
  )
}
