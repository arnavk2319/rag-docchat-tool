import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { Citation } from '../../api/contract'
import { ApiError, GENERIC_MESSAGE } from '../../api/errors'
import { StreamIncompleteError, streamQuery } from '../../api/sse'
import { SESSIONS_KEY, useCreateSession, useSession } from '../sessions/hooks'
import { useScope } from '../scope/ScopeContext'
import AssistantMessage from './AssistantMessage'
import Composer from './Composer'
import { initialState, reducer } from './reducer'

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

export default function Chat({
  sessionId,
  onSessionCreated,
  onNewChat,
  activeChunkId,
  onOpenCitation,
}: {
  sessionId?: string
  /** Called after POST /v1/sessions succeeded on the first send of a new chat. */
  onSessionCreated?: (id: string) => void
  onNewChat?: () => void
  activeChunkId?: string
  onOpenCitation: (c: Citation) => void
}) {
  const { selected, nameOf } = useScope()
  const [state, dispatch] = useReducer(reducer, initialState)
  const abortRef = useRef<AbortController | null>(null)
  const busyRef = useRef(false)
  const [restore, setRestore] = useState<{ n: number; text: string } | null>(null)
  const qc = useQueryClient()
  const createSession = useCreateSession()
  // Session created by this very chat: its history is local, so never load it from the server.
  const [own, setOwn] = useState<string | undefined>(undefined)
  const loaded = useSession(sessionId, sessionId !== own)
  const notFound = loaded.isError && loaded.error instanceof ApiError && loaded.error.status === 404
  const hasMessages = state.messages.length > 0
  const scopeKey = selected.join('|')

  // "Scope changed" note when ticks change in a non-empty chat.
  const prevKey = useRef(scopeKey)
  useEffect(() => {
    if (prevKey.current === scopeKey) return
    prevKey.current = scopeKey
    if (hasMessages) {
      dispatch({ type: 'note', text: `Scope changed: now ${plural(selected.length, 'document')}` })
    }
  }, [scopeKey, hasMessages, selected.length])

  useEffect(() => () => abortRef.current?.abort(), [])

  const history = loaded.data
  useEffect(() => {
    if (history) dispatch({ type: 'load', messages: history.messages })
  }, [history])

  const send = useCallback(
    async (question: string) => {
      if (busyRef.current) return
      busyRef.current = true
      const ac = new AbortController()
      abortRef.current = ac
      dispatch({ type: 'send', question })
      try {
        let sid = sessionId
        if (!sid) {
          // SSE events carry no session_id, so the session is created explicitly first.
          sid = (await createSession.mutateAsync()).session_id
          setOwn(sid)
          if (ac.signal.aborted) throw new Error('aborted')
          onSessionCreated?.(sid)
        }
        for await (const event of streamQuery(
          { question, doc_ids: selected, session_id: sid },
          ac.signal,
        )) {
          dispatch({ type: 'event', event })
        }
        if (ac.signal.aborted) dispatch({ type: 'stopped' })
        else void qc.invalidateQueries({ queryKey: SESSIONS_KEY })
      } catch (e) {
        if (ac.signal.aborted) dispatch({ type: 'stopped' })
        else {
          const message =
            e instanceof ApiError || e instanceof StreamIncompleteError
              ? e.message
              : GENERIC_MESSAGE
          dispatch({ type: 'failed', message })
          setRestore((r) => ({ n: (r?.n ?? 0) + 1, text: question }))
        }
      } finally {
        busyRef.current = false
      }
    },
    [selected, sessionId, createSession, onSessionCreated, qc],
  )

  if (notFound) {
    return (
      <section className="flex min-h-0 min-w-0 flex-col" aria-label="Chat">
        <div role="alert" className="m-auto max-w-[44ch] text-center text-muted">
          <b className="mb-1 block text-base text-text">Conversation not found</b>
          It may have been deleted.
          <div className="mt-3">
            <button
              type="button"
              onClick={onNewChat}
              className="rounded-md border border-line bg-panel px-3 py-1.5 font-semibold text-text"
            >
              New chat
            </button>
          </div>
        </div>
      </section>
    )
  }

  const noScope = selected.length === 0
  const first = selected.length === 1 ? nameOf(selected[0]) : undefined
  const placeholder = noScope
    ? 'Select a document to start'
    : first
      ? `Ask about ${first}`
      : `Ask about ${plural(selected.length, 'document')}`
  const hint = noScope
    ? 'Tick at least one ready document in the left pane.'
    : state.streaming
      ? 'Reading your documents…'
      : 'Answers cite the passages they came from.'

  return (
    <section className="flex min-h-0 min-w-0 flex-col" aria-label="Chat">
      <div className="flex flex-col gap-0.5 border-b border-line px-5 py-3">
        <h1 className="m-0 text-base font-bold tracking-tight">Chat</h1>
        <div className="overflow-hidden text-[12.5px] text-ellipsis whitespace-nowrap text-muted">
          {noScope ? (
            'No documents selected'
          ) : (
            <>
              Asking across{' '}
              <b className="text-primary-text">{plural(selected.length, 'document')}</b>
            </>
          )}
        </div>
      </div>
      <div
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-5"
        role="log"
        aria-live="polite"
        aria-label="Conversation"
      >
        {!hasMessages && (
          <div className="m-auto max-w-[44ch] text-center text-muted">
            <b className="mb-1 block text-base text-text">Ask your first question</b>
            The answer will cite the passages it used from your selected documents.
          </div>
        )}
        {state.messages.map((m) => {
          if (m.role === 'user') {
            return (
              <div
                key={m.id}
                className="max-w-[min(560px,80%)] self-end rounded-[16px_16px_4px_16px] bg-primary px-3.5 py-2 break-words whitespace-pre-wrap text-primary-ink"
              >
                {m.text}
              </div>
            )
          }
          if (m.role === 'note') {
            return (
              <div
                key={m.id}
                className="self-center rounded-full border border-line bg-side px-3 py-[3px] text-xs text-muted"
              >
                {m.text}
              </div>
            )
          }
          return (
            <AssistantMessage
              key={m.id}
              msg={m}
              activeChunkId={activeChunkId}
              onOpenCitation={onOpenCitation}
            />
          )
        })}
      </div>
      <Composer
        placeholder={placeholder}
        hint={hint}
        noScope={noScope}
        streaming={state.streaming}
        onSend={(q) => void send(q)}
        restore={restore}
        onStop={() => abortRef.current?.abort()}
      />
    </section>
  )
}
