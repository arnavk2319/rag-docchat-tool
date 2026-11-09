import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { ChunkPassage, Citation } from '../../api/contract'
import { ApiError, messageFor } from '../../api/errors'

type Result =
  | { key: string; kind: 'ok'; passage: ChunkPassage }
  | { key: string; kind: 'not_found' }
  | { key: string; kind: 'error'; message: string }

const keyOf = (c: Citation) => `${c.doc_id}/${c.chunk_id}`

/** Passage text with the highlight in <mark>; React text nodes only, never HTML. */
export function Passage({ passage }: { passage: ChunkPassage }) {
  const { highlight: h } = passage
  // Backend offsets count code points (Python); String.slice counts UTF-16 units.
  const chars = Array.from(passage.text)
  const text = passage.text
  const valid =
    Number.isInteger(h.start) &&
    Number.isInteger(h.end) &&
    h.start >= 0 &&
    h.start < h.end &&
    h.end <= chars.length
  return (
    <div className="rounded-sm border border-line bg-panel px-4 py-3.5 text-[13.5px] leading-[1.7] break-words whitespace-pre-wrap">
      {valid ? (
        <>
          {chars.slice(0, h.start).join('')}
          <mark className="rounded-[2px] bg-mark px-0.5 text-inherit">
            {chars.slice(h.start, h.end).join('')}
          </mark>
          {chars.slice(h.end).join('')}
        </>
      ) : (
        text
      )}
    </div>
  )
}

export default function SourcePane({ citation }: { citation: Citation | null }) {
  const [result, setResult] = useState<Result | null>(null)
  const key = citation ? keyOf(citation) : null

  useEffect(() => {
    if (!citation || !key) return
    let live = true
    api
      .GET('/v1/documents/{doc_id}/chunks/{chunk_id}', {
        params: { path: { doc_id: citation.doc_id, chunk_id: citation.chunk_id } },
      })
      .then((r) => {
        if (!live) return
        if (r.data) setResult({ key, kind: 'ok', passage: r.data })
        else if (r.response.status === 404) setResult({ key, kind: 'not_found' })
        else setResult({ key, kind: 'error', message: messageFor(null) })
      })
      .catch((e: unknown) => {
        if (live)
          setResult({
            key,
            kind: 'error',
            message: e instanceof ApiError ? e.message : messageFor(null),
          })
      })
    return () => {
      live = false
    }
  }, [citation, key])

  const current = result && result.key === key ? result : null
  let body
  if (!citation) {
    body = (
      <p className="py-2 text-[13px] text-muted">
        Choose a citation under an answer to read the passage it came from, with the cited text
        highlighted.
      </p>
    )
  } else if (!current) {
    body = (
      <p role="status" className="py-2 text-[13px] text-muted">
        Loading passage…
      </p>
    )
  } else if (current.kind === 'not_found') {
    body = (
      <p role="alert" className="py-2 text-[13px] text-warn">
        This passage is no longer available. The document may have been removed.
      </p>
    )
  } else if (current.kind === 'error') {
    body = (
      <p role="alert" className="py-2 text-[13px] text-warn">
        {current.message}
      </p>
    )
  } else {
    const p = current.passage
    const where = [
      p.page != null ? `page ${p.page}` : p.sheet ? `sheet ${p.sheet}` : null,
      `characters ${citation.span.char_start}–${citation.span.char_end}`,
    ]
      .filter(Boolean)
      .join(' · ')
    body = (
      <>
        <p className="m-0 mb-0.5 font-bold">{p.filename}</p>
        <div className="mb-3 font-mono text-[11.5px] text-muted">{where}</div>
        <Passage passage={p} />
      </>
    )
  }

  return (
    <aside
      className="flex min-h-0 flex-col border-l border-line bg-side"
      aria-label="Source passage"
    >
      <header className="border-b border-line px-4 pt-3.5 pb-2.5">
        <h2 className="m-0 text-[11px] font-bold tracking-[.08em] text-muted uppercase">
          Source passage
        </h2>
      </header>
      <div className="flex-1 overflow-auto p-4">{body}</div>
    </aside>
  )
}
