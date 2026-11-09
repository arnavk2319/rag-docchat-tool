import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import type { Citation } from '../../api/contract'
import { ApiError, GENERIC_MESSAGE } from '../../api/errors'
import { decodeEntities } from '../../api/text'
import CitationChips from '../chat/CitationChips'
import { useScope } from '../scope/ScopeContext'
import { fetchCompare, fetchSentiment, fetchSummary } from './api'
import { INTEL_KINDS } from './flag'
import type { IntelKind } from './flag'
import type { CompareResponse, ComparePoint, DocSentiment, DocSummary } from './types'

const MAX_COMPARE = 5
const STOP_CODES = new Set(['spend_cap_exceeded', 'rate_limited', 'budget_exceeded'])

export function IntelNav({ chatPath = '/chat' }: { chatPath?: string }) {
  return (
    <nav aria-label="Views" className="flex gap-1 border-b border-line bg-panel px-3 py-2">
      <NavLink
        to={chatPath}
        className="rounded-sm px-2.5 py-1 font-semibold text-muted aria-[current=page]:bg-primary-soft aria-[current=page]:text-primary-text"
      >
        Chat
      </NavLink>
      {INTEL_KINDS.map((k) => (
        <NavLink
          key={k.kind}
          to={`/${k.kind}`}
          className="rounded-sm px-2.5 py-1 font-semibold text-muted aria-[current=page]:bg-primary-soft aria-[current=page]:text-primary-text"
        >
          {k.label}
        </NavLink>
      ))}
    </nav>
  )
}

const errCode = (e: unknown): string => (e instanceof ApiError ? e.code : 'internal')
const errMessage = (e: unknown): string => (e instanceof ApiError ? e.message : GENERIC_MESSAGE)

type Row<T> =
  | { id: string; state: 'running' }
  | { id: string; state: 'ok'; data: T }
  | { id: string; state: 'error'; message: string }

function Flags({ partial, abstained }: { partial?: boolean; abstained?: boolean }) {
  return (
    <>
      {partial && (
        <span className="rounded-full bg-warn-soft px-2 py-0.5 text-xs font-semibold text-warn">
          Partial result
        </span>
      )}
      {abstained && (
        <p className="text-[13px] text-muted">
          The model could not give a confident answer from this document.
        </p>
      )}
    </>
  )
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section
      aria-label={title}
      className="mb-3 flex flex-col gap-2 rounded-md border border-line bg-panel p-3"
    >
      <h3 className="m-0 text-sm font-semibold break-words text-text">{title}</h3>
      {children}
    </section>
  )
}

function Failure({ message }: { message: string }) {
  return (
    <p role="alert" className="rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">
      {message}
    </p>
  )
}

function Points({
  title,
  points,
  onOpen,
}: {
  title: string
  points: ComparePoint[]
  onOpen: (c: Citation) => void
}) {
  return (
    <Card title={title}>
      {points.length === 0 && <p className="text-[13px] text-muted">None found.</p>}
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {points.map((p, i) => (
          <li key={i} className="flex flex-col gap-1.5">
            <span className="break-words whitespace-pre-wrap">{decodeEntities(p.point)}</span>
            <CitationChips citations={p.citations} onOpen={onOpen} />
          </li>
        ))}
      </ul>
    </Card>
  )
}

export default function DocIntelView({
  kind,
  onOpenCitation,
}: {
  kind: IntelKind
  onOpenCitation: (c: Citation) => void
}) {
  const scope = useScope()
  const label = INTEL_KINDS.find((k) => k.kind === kind)!.label
  const hasReady = scope.docs.some((d) => d.ready)
  const [rows, setRows] = useState<Row<DocSummary | DocSentiment>[]>([])
  const [cmp, setCmp] = useState<
    | { state: 'running' }
    | { state: 'ok'; data: CompareResponse }
    | { state: 'error'; message: string }
    | null
  >(null)
  const [focus, setFocus] = useState('')
  const [running, setRunning] = useState(false)
  const busy = useRef(false) // synchronous guard against double clicks
  const abort = useRef(new AbortController())
  useEffect(() => {
    const c = new AbortController()
    abort.current = c
    return () => c.abort() // leaving the view stops the per-document loop (no further paid calls)
  }, [])

  const ids = scope.selected
  const perDoc = kind !== 'compare'
  const tooFew = kind === 'compare' && ids.length < 2
  const tooMany = kind === 'compare' && ids.length > MAX_COMPARE
  const disabled = running || ids.length === 0 || tooFew || tooMany

  async function run() {
    if (busy.current) return
    busy.current = true
    setRunning(true)
    try {
      if (kind === 'compare') {
        setCmp({ state: 'running' })
        try {
          setCmp({ state: 'ok', data: await fetchCompare(ids, focus.trim()) })
        } catch (e) {
          setCmp({ state: 'error', message: errMessage(e) })
        }
        return
      }
      const fetcher = kind === 'summary' ? fetchSummary : fetchSentiment
      setRows(ids.map((id) => ({ id, state: 'running' as const })))
      for (const id of ids) {
        if (abort.current.signal.aborted) return
        try {
          const data = await fetcher(id)
          setRows((r) => r.map((x) => (x.id === id ? { id, state: 'ok', data } : x)))
        } catch (e) {
          const message = errMessage(e)
          if (STOP_CODES.has(errCode(e))) {
            // Limits apply to every later call too: stop spending and mark the rest as skipped.
            setRows((r) =>
              r.map((x) =>
                x.id === id
                  ? { id, state: 'error', message }
                  : x.state === 'running'
                    ? { id: x.id, state: 'error', message: `Not run. ${message}` }
                    : x,
              ),
            )
            return
          }
          setRows((r) => r.map((x) => (x.id === id ? { id, state: 'error', message } : x)))
        }
      }
    } finally {
      busy.current = false
      setRunning(false)
    }
  }

  const name = (id: string) => scope.nameOf(id) ?? id

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4" data-testid={`intel-${kind}`}>
      <h2 className="m-0 mb-1 text-base font-semibold text-text">{label}</h2>
      {!hasReady ? (
        <p className="text-muted">No ready documents yet. Upload a document to get started.</p>
      ) : (
        <>
          <p className="mb-2 text-[13px] text-muted">
            {kind === 'compare'
              ? 'Compare 2 to 5 ticked documents.'
              : `Runs ${label.toLowerCase()} on each ticked document.`}{' '}
            <b>Uses your daily budget</b> (one model call
            {perDoc ? ' per document' : ''}). Nothing runs until you press the button.
          </p>
          {kind === 'compare' && (
            <label className="mb-2 flex flex-col gap-1 text-[13px] text-muted">
              Focus (optional)
              <input
                value={focus}
                maxLength={200}
                onChange={(e) => setFocus(e.target.value)}
                className="rounded-md border border-line bg-panel px-2 py-1.5 text-text"
              />
            </label>
          )}
          <div className="mb-3 flex items-center gap-3">
            <button
              type="button"
              disabled={disabled}
              onClick={() => void run()}
              className="rounded-md bg-primary px-3 py-1.5 font-semibold text-primary-ink disabled:opacity-50"
            >
              {running ? 'Running…' : `Run ${label.toLowerCase()}`}
            </button>
            <span className="text-xs text-muted">
              {ids.length === 0
                ? 'Tick documents in the left pane.'
                : tooFew
                  ? 'Tick at least 2 documents.'
                  : tooMany
                    ? `Tick at most ${MAX_COMPARE} documents.`
                    : `${ids.length} selected`}
            </span>
          </div>
        </>
      )}

      {perDoc &&
        rows.map((r) => (
          <Card key={r.id} title={name(r.id)}>
            {r.state === 'running' && (
              <p role="status" className="text-muted">
                Working…
              </p>
            )}
            {r.state === 'error' && <Failure message={r.message} />}
            {r.state === 'ok' && kind === 'summary' && (
              <SummaryBody data={r.data as DocSummary} onOpen={onOpenCitation} />
            )}
            {r.state === 'ok' && kind === 'sentiment' && (
              <SentimentBody data={r.data as DocSentiment} onOpen={onOpenCitation} />
            )}
          </Card>
        ))}

      {kind === 'compare' && cmp?.state === 'running' && (
        <p role="status" className="text-muted">
          Comparing…
        </p>
      )}
      {kind === 'compare' && cmp?.state === 'error' && <Failure message={cmp.message} />}
      {kind === 'compare' && cmp?.state === 'ok' && (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Flags partial={cmp.data.partial} abstained={cmp.data.abstained} />
          </div>
          <Points title="Similarities" points={cmp.data.similarities} onOpen={onOpenCitation} />
          <Points title="Differences" points={cmp.data.differences} onOpen={onOpenCitation} />
        </>
      )}
    </div>
  )
}

function SummaryBody({ data, onOpen }: { data: DocSummary; onOpen: (c: Citation) => void }) {
  return (
    <>
      <Flags partial={data.partial} abstained={data.abstained} />
      {data.summary && (
        <p className="m-0 break-words whitespace-pre-wrap">{decodeEntities(data.summary)}</p>
      )}
      <CitationChips citations={data.citations} onOpen={onOpen} />
    </>
  )
}

function SentimentBody({ data, onOpen }: { data: DocSentiment; onOpen: (c: Citation) => void }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-primary-soft px-2.5 py-0.5 text-xs font-semibold break-words text-primary-text">
          {data.label}
        </span>
        <span className="text-xs text-muted">Confidence {Math.round(data.confidence * 100)}%</span>
        <Flags partial={data.partial} />
      </div>
      {data.abstained && <Flags abstained />}
      <p className="m-0 break-words whitespace-pre-wrap">{decodeEntities(data.rationale)}</p>
      <CitationChips citations={data.citations} onOpen={onOpen} />
    </>
  )
}
