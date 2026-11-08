import clsx from 'clsx'
import { useNavigate } from 'react-router-dom'
import { ApiError, GENERIC_MESSAGE } from '../../api/errors'
import { useSessions } from './hooks'

/** Conversations section of the left pane. Titles are plain text children only. */
export function SessionList({ activeId }: { activeId?: string }) {
  const nav = useNavigate()
  const q = useSessions()
  const items = q.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <section aria-label="Conversations">
      <div className="flex items-center justify-between px-4 pt-3 pb-1 text-[11px] font-bold tracking-[.08em] text-muted uppercase">
        <span>Conversations</span>
        <button
          type="button"
          onClick={() => void nav('/chat')}
          className="rounded-sm px-1 py-0.5 text-xs font-semibold tracking-normal text-link normal-case"
        >
          + New
        </button>
      </div>
      <div className="flex flex-col gap-px px-2 pb-2">
        {q.isPending && <p className="m-0 px-2 py-2 text-muted">Loading…</p>}
        {q.isError && (
          <p role="alert" className="m-0 px-2 py-2 text-muted">
            {q.error instanceof ApiError ? q.error.message : GENERIC_MESSAGE}
          </p>
        )}
        {q.isSuccess && items.length === 0 && (
          <p className="m-0 px-2 py-2 text-muted">No conversations yet.</p>
        )}
        {items.map((s) => (
          <button
            key={s.session_id}
            type="button"
            aria-current={s.session_id === activeId}
            onClick={() => void nav(`/chat/${encodeURIComponent(s.session_id)}`)}
            className={clsx(
              'flex w-full flex-col gap-px rounded-md border-0 bg-transparent px-2.5 py-2 text-left hover:bg-line-soft',
              s.session_id === activeId && 'bg-primary-soft',
            )}
          >
            <span className="truncate font-semibold">{s.title}</span>
            <time dateTime={s.updated_at} className="text-xs text-muted">
              {new Date(s.updated_at).toLocaleString()}
            </time>
          </button>
        ))}
        {q.hasNextPage && (
          <button
            type="button"
            disabled={q.isFetchingNextPage}
            onClick={() => void q.fetchNextPage()}
            className="rounded-md px-2.5 py-2 text-left text-xs font-semibold text-link"
          >
            Load more
          </button>
        )}
      </div>
    </section>
  )
}
