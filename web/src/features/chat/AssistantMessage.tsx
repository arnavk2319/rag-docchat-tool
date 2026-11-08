import type { Citation } from '../../api/contract'
import CitationChips from './CitationChips'
import { stopInfo } from './reducer'
import type { AssistantMsg } from './reducer'
import TraceView from './TraceView'

const TONE = { ok: 'text-faint', warn: 'text-warn', error: 'text-red-800' } as const

export default function AssistantMessage({
  msg,
  activeChunkId,
  onOpenCitation,
}: {
  msg: AssistantMsg
  activeChunkId?: string
  onOpenCitation: (c: Citation) => void
}) {
  const streaming = msg.status === 'streaming'
  let stop: { text: string; tone: keyof typeof TONE; reason: string } | null = null
  if (msg.status === 'stopped') {
    stop = { text: 'Stopped by you', tone: 'warn', reason: 'user_stopped' }
  } else if (msg.status === 'done' && msg.stoppedReason) {
    const info = stopInfo(msg.stoppedReason)
    const u = msg.usage
    const usage = u ? ` · ${u.steps} steps · ${u.tokens} tokens · $${u.usd.toFixed(4)}` : ''
    stop = { text: info.label + usage, tone: info.tone, reason: msg.stoppedReason }
  }
  return (
    <div className="flex max-w-[min(640px,92%)] flex-col gap-2 self-start" data-testid="assistant">
      <TraceView trace={msg.trace} streaming={streaming} />
      {msg.answer && (
        <div
          className="rounded-[16px_16px_16px_4px] border border-line bg-panel px-3.5 py-2.5 leading-relaxed break-words whitespace-pre-wrap"
          data-testid="answer"
        >
          {msg.answer}
        </div>
      )}
      {msg.errorText && (
        <div role="alert" className="rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">
          {msg.errorText}
        </div>
      )}
      {!streaming && (
        <CitationChips
          citations={msg.citations}
          activeChunkId={activeChunkId}
          onOpen={onOpenCitation}
        />
      )}
      {stop && (
        <div
          className={`font-mono text-[11.5px] ${TONE[stop.tone]}`}
          data-stop={stop.reason}
          data-testid="stop-reason"
        >
          {stop.text}
        </div>
      )}
    </div>
  )
}
