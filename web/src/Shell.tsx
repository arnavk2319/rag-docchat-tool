import { useState } from 'react'
import { useLocation, useParams } from 'react-router-dom'
import type { Citation } from './api/contract'
import Chat from './features/chat/Chat'
import { DocumentsPanel } from './features/documents/DocumentsPanel'
import { ScopeProvider, useScope } from './features/scope/ScopeContext'
import SourcePane from './features/source/SourcePane'
import { useNarrow } from './useNarrow'

type View = 'docs' | 'chat' | 'source'
const TABS: { v: View; label: string }[] = [
  { v: 'docs', label: 'Documents' },
  { v: 'chat', label: 'Chat' },
  { v: 'source', label: 'Source' },
]

function Workspace({ sessionId, initialView }: { sessionId?: string; initialView: View }) {
  const narrow = useNarrow()
  const scope = useScope()
  const [view, setView] = useState<View>(initialView)
  const [citation, setCitation] = useState<Citation | null>(null)

  const left = (
    <aside
      className="flex min-h-0 flex-col border-r border-line bg-side"
      aria-label="Documents and conversations"
    >
      <div className="px-4 pt-3.5 pb-2.5">
        <b className="text-[17px] tracking-tight text-primary-text">DocChat</b>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <DocumentsPanel
          selected={scope.selected}
          onToggle={scope.toggle}
          onSelectAll={scope.selectAll}
          onClear={scope.clear}
        />
      </div>
    </aside>
  )
  const chat = (
    <Chat
      key={sessionId ?? 'new'}
      sessionId={sessionId}
      activeChunkId={citation?.chunk_id}
      onOpenCitation={(c) => {
        setCitation(c)
        setView('source')
      }}
    />
  )
  const source = <SourcePane citation={citation} />

  return (
    <div className="mx-auto max-w-[1320px]">
      <div
        className={`overflow-hidden rounded-md border border-line bg-panel ${
          narrow
            ? 'grid h-[calc(100dvh-100px)] min-h-[480px] grid-cols-1'
            : 'grid h-[min(780px,calc(100dvh-32px))] min-h-[560px] grid-cols-[300px_minmax(0,1fr)_340px]'
        }`}
      >
        {narrow ? (
          <>
            {view === 'docs' && left}
            {view === 'chat' && chat}
            {view === 'source' && source}
          </>
        ) : (
          <>
            {left}
            {chat}
            {source}
          </>
        )}
      </div>
      {narrow && (
        <div
          role="tablist"
          aria-label="Panes"
          className="mt-2 grid grid-cols-3 gap-1 rounded-md border border-line bg-panel p-1"
        >
          {TABS.map((t) => (
            <button
              key={t.v}
              type="button"
              role="tab"
              aria-selected={view === t.v}
              onClick={() => setView(t.v)}
              className="rounded-sm p-2 font-semibold text-muted aria-selected:bg-primary-soft aria-selected:text-primary-text"
            >
              {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** Layout route element: three-pane workspace shared by /documents and /chat/:sessionId?. */
export default function Shell() {
  const { sessionId } = useParams()
  const { pathname } = useLocation()
  return (
    <ScopeProvider sessionId={sessionId}>
      <Workspace
        sessionId={sessionId}
        initialView={pathname.startsWith('/documents') ? 'docs' : 'chat'}
      />
    </ScopeProvider>
  )
}
