import { useState } from 'react'
import type { ChangeEvent } from 'react'
import clsx from 'clsx'
import type { DocumentOut } from '../../api/contract'
import { ApiError, messageFor } from '../../api/errors'
import { useDocuments, useRemoveDocument, useUploadDocuments } from './hooks'
import { UploadErrors } from './UploadErrors'
import { ACCEPT, describeDoc, MAX_FILES, precheck } from './validate'
import type { FileProblem } from './validate'

export interface DocumentsPanelProps {
  selected: string[]
  onToggle(id: string): void
  onSelectAll(ids: string[]): void
  onClear(): void
  /** Poll interval while documents are indexing (ms). */
  pollMs?: number
}

const PILL: Record<DocumentOut['status'], { label: string; cls: string }> = {
  ready: { label: 'Ready', cls: 'bg-primary-soft text-primary-text' },
  pending: { label: 'Indexing', cls: 'bg-warn-soft text-warn' },
  processing: { label: 'Indexing', cls: 'bg-warn-soft text-warn' },
  failed: { label: 'Failed', cls: 'bg-warn-soft text-warn' },
}

const linkBtn = 'rounded-sm px-1 py-0.5 text-xs font-semibold text-link'
const smallBtn = 'rounded-md border border-line bg-panel px-2 py-1 font-semibold text-text'

export function DocumentsPanel({
  selected,
  onToggle,
  onSelectAll,
  onClear,
  pollMs,
}: DocumentsPanelProps) {
  const docs = useDocuments(pollMs)
  const upload = useUploadDocuments()
  const remove = useRemoveDocument()
  const [dups, setDups] = useState<string[]>([])
  const [problems, setProblems] = useState<FileProblem[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [delError, setDelError] = useState<{ id: string; message: string } | null>(null)

  const items = docs.data?.items ?? []
  const readyIds = items.filter((d) => d.status === 'ready').map((d) => d.doc_id)

  const onFiles = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    e.target.value = ''
    if (files.length === 0) return
    const { ok, problems: pre } = precheck(files)
    setProblems(pre)
    setDups([])
    setNotice(files.length > MAX_FILES ? messageFor('too_many_files') : null)
    if (ok.length === 0) return
    upload.mutate(ok, {
      onSuccess: (r) => {
        setProblems((p) => [...p, ...r.problems])
        setDups(r.duplicates)
      },
      onError: (err) => setNotice(err instanceof ApiError ? err.message : messageFor('internal')),
    })
  }

  const confirmDelete = (d: DocumentOut) => {
    setDelError(null)
    setConfirmId(null)
    remove.mutate(d.doc_id, {
      onSuccess: () => {
        if (selected.includes(d.doc_id)) onToggle(d.doc_id)
      },
      onError: (err) => setDelError({ id: d.doc_id, message: err.message }),
    })
  }

  return (
    <section aria-labelledby="docs-h" className="flex flex-col bg-side">
      <div className="flex items-center justify-between px-4 pt-3.5 pb-1">
        <h2 id="docs-h" className="text-[11px] font-bold tracking-[0.08em] text-muted uppercase">
          Documents
        </h2>
        <span className="flex items-center">
          <button type="button" className={linkBtn} onClick={() => onSelectAll(readyIds)}>
            All
          </button>
          <button type="button" className={linkBtn} onClick={onClear}>
            None
          </button>
          <label className="ml-2 cursor-pointer rounded-md border border-primary bg-primary px-3 py-1.5 text-[13px] font-semibold text-primary-ink focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-link hover:brightness-110">
            Upload
            <input
              type="file"
              multiple
              accept={ACCEPT}
              className="sr-only"
              aria-label="Upload documents"
              onChange={onFiles}
              disabled={upload.isPending}
            />
          </label>
        </span>
      </div>

      {upload.isPending && (
        <p role="status" className="px-4 py-1 text-xs text-muted">
          Uploading…
        </p>
      )}
      {notice && (
        <p role="alert" className="mx-2 my-1 rounded-md bg-warn-soft p-2 text-xs text-warn">
          {notice}
        </p>
      )}
      <UploadErrors problems={problems} />
      {dups.length > 0 && (
        <p role="status" className="mx-2 my-1 text-xs text-muted">
          Already uploaded: {dups.join(', ')}
        </p>
      )}

      <div className="flex flex-col gap-0.5 px-2 py-0.5">
        {docs.isPending && (
          <p role="status" className="p-2 text-muted">
            Loading documents…
          </p>
        )}
        {docs.isError && (
          <p role="alert" className="p-2 text-warn">
            {docs.error instanceof ApiError ? docs.error.message : messageFor('internal')}
          </p>
        )}
        {docs.isSuccess && items.length === 0 && (
          <p className="p-2 text-muted">
            No documents yet. Upload a PDF, DOCX, TXT, CSV or XLSX file to start.
          </p>
        )}
        {items.map((d) => {
          const ready = d.status === 'ready'
          const on = ready && selected.includes(d.doc_id)
          const pill = PILL[d.status]
          const cbId = `cb-${d.doc_id}`
          return (
            <div key={d.doc_id}>
              <div
                className={clsx(
                  'grid grid-cols-[auto_1fr_auto_auto] items-center gap-2.5 rounded-md p-2 hover:bg-line-soft',
                  on && 'bg-primary-soft',
                  !ready && 'opacity-75',
                )}
              >
                <input
                  id={cbId}
                  type="checkbox"
                  className="m-0 size-4 accent-primary"
                  checked={on}
                  disabled={!ready}
                  onChange={() => onToggle(d.doc_id)}
                />
                <label
                  htmlFor={cbId}
                  className={clsx('min-w-0', ready ? 'cursor-pointer' : 'cursor-not-allowed')}
                >
                  <span className="block truncate font-semibold">{d.filename}</span>
                  <span className="text-xs text-muted">
                    {describeDoc(d.filename, d.size, d.pages_or_sheets)}
                  </span>
                </label>
                <span
                  className={clsx(
                    'rounded-full px-2 py-px text-[11px] font-semibold whitespace-nowrap',
                    pill.cls,
                  )}
                >
                  {pill.label}
                </span>
                <button
                  type="button"
                  aria-label={`Delete ${d.filename}`}
                  className="rounded-sm px-1.5 text-muted hover:text-warn"
                  onClick={() => {
                    setDelError(null)
                    setConfirmId(d.doc_id)
                  }}
                >
                  ×
                </button>
              </div>
              {confirmId === d.doc_id && (
                <div
                  role="alertdialog"
                  aria-label={`Confirm delete ${d.filename}`}
                  className="mx-2 mb-1 flex flex-wrap items-center gap-2 rounded-md bg-warn-soft p-2 text-xs text-warn"
                >
                  <span className="break-all">Delete {d.filename}?</span>
                  <button type="button" className={smallBtn} onClick={() => confirmDelete(d)}>
                    Confirm delete
                  </button>
                  <button type="button" className={smallBtn} onClick={() => setConfirmId(null)}>
                    Cancel
                  </button>
                </div>
              )}
              {delError?.id === d.doc_id && (
                <p role="alert" className="mx-2 mb-1 rounded-md bg-warn-soft p-2 text-xs text-warn">
                  {delError.message}
                </p>
              )}
            </div>
          )
        })}
      </div>

      <p className="px-4 py-3 text-xs text-muted">Demo data resets in 24 hours.</p>
    </section>
  )
}
