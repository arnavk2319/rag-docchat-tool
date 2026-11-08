import { useState } from 'react'
import type { KeyboardEvent } from 'react'

export const MAX_QUESTION = 2000

export default function Composer({
  placeholder,
  hint,
  noScope,
  streaming,
  onSend,
  onStop,
}: {
  placeholder: string
  hint: string
  noScope: boolean
  streaming: boolean
  onSend: (q: string) => void
  onStop: () => void
}) {
  const [text, setText] = useState('')
  const trimmed = text.trim()
  const tooLong = trimmed.length > MAX_QUESTION
  const canSend = !noScope && !streaming && trimmed.length > 0 && !tooLong

  const submit = () => {
    if (!canSend) return
    onSend(trimmed)
    setText('')
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }
  const message = tooLong ? `Question is too long (max ${MAX_QUESTION} characters).` : hint

  return (
    <div className="border-t border-line-soft px-5 pt-3 pb-4">
      <div className="flex items-end gap-2 rounded-[16px] border border-line bg-panel py-1.5 pr-1.5 pl-3.5 focus-within:border-primary focus-within:shadow-[0_0_0_3px_var(--color-primary-soft)]">
        <textarea
          aria-label="Your question"
          rows={1}
          className="max-h-[120px] flex-1 resize-none border-0 bg-transparent py-1.5 text-text outline-0 placeholder:text-faint"
          placeholder={placeholder}
          value={text}
          disabled={noScope || streaming}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        {streaming && (
          <button
            type="button"
            onClick={onStop}
            className="rounded-md border border-line bg-panel px-3 py-1.5 text-[13px] font-semibold hover:border-primary"
          >
            Stop
          </button>
        )}
        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          className="rounded-md border border-primary bg-primary px-3 py-1.5 text-[13px] font-semibold text-primary-ink disabled:cursor-not-allowed disabled:opacity-45"
        >
          Send
        </button>
      </div>
      <div className="mx-1 mt-1.5 min-h-[18px] text-xs text-muted" aria-live="polite">
        {message}
      </div>
    </div>
  )
}
