import { useAui, useAuiState } from '@assistant-ui/react'
import { ListRestartIcon } from 'lucide-react'

import { TooltipIconButton } from '@/components/assistant-ui/elements/tooltip-icon-button'
import type { ThreadStorage } from '@/lib/thread-storage'

/** Starts over: stops any answer in progress, forgets the stored conversation
 * and empties the thread. Hidden while the thread is already empty. */
export function NewConversation({ storage }: { storage: ThreadStorage }) {
  const aui = useAui()
  const isEmpty = useAuiState((s) => s.thread.isEmpty)
  if (isEmpty) return null
  return (
    <TooltipIconButton
      tooltip="New conversation"
      side="left"
      variant="outline"
      className="absolute top-3 right-6 z-20 size-8 rounded-full p-2"
      onClick={() => {
        if (aui.thread.getState().isRunning) aui.thread.cancelRun()
        storage.clear()
        aui.thread.reset()
      }}
    >
      <ListRestartIcon />
    </TooltipIconButton>
  )
}
