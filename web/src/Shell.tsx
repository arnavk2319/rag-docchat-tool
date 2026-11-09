import { useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import type { Citation } from './api/contract'
import Chat from './features/chat/Chat'
import DocIntelView, { IntelNav } from './features/docintel/DocIntelView'
import { docIntelEnabled } from './features/docintel/flag'
import type { IntelKind } from './features/docintel/flag'
import { DocumentsPanel } from './features/documents/DocumentsPanel'
import { ScopeProvider, useScope } from './features/scope/ScopeContext'
import { SessionList } from './features/sessions/SessionList'
import SourcePane from './features/source/SourcePane'
import { useNarrow } from './useNarrow'

type View = 'docs' | 'chat' | 'source'
const TABS: { v: View; label: string }[] = [
  { v: 'docs', label: 'Documents' },
  { v: 'chat', label: 'Chat' },
  { v: 'source', label: 'Source' },
]

function Workspace({
  sessionId,
  initialView,
  intel,
}: {
  sessionId?: string
  initialView: View
  intel?: IntelKind
}) {
  const narrow = useNarrow()
  const scope = useScope()
  const [view, setView] = useState<View>(initialView)
  const [citation, setCitation] = useState<Citation | null>(null)
  const nav = useNavigate()
  // Chat remounts on a user-driven switch (aborting any stream) but not when the route merely
  // adopts the session this chat just created.
  // Chat stays mounted (hidden) while an intel view shows, so remember its session.
  const [chatSid, setChatSid] = useState(sessionId)
  if (!intel && sessionId !== chatSid) setChatSid(sessionId)
  const sid = intel ? chatSid : sessionId
  const [inst, setInst] = useState({ key: 0, sid: sid, own: undefined as string | undefined })
  if (sid !== inst.sid) {
    const adopted = sid !== undefined && sid === inst.own
    setInst({
      key: adopted ? inst.key : inst.key + 1,
      sid: sid,
      own: adopted ? inst.own : undefined,
    })
  }

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
        <SessionList activeId={sessionId} />
      </div>
    </aside>
  )
  const openCitation = (c: Citation) => {
    setCitation(c)
    setView('source')
  }
  const chatPane = (
    <Chat
      key={inst.key}
      sessionId={sid}
      onSessionCreated={(id) => {
        setInst((i) => ({ ...i, own: id }))
        if (intel) setChatSid(id)
        else void nav(`/chat/${encodeURIComponent(id)}`, { replace: true })
      }}
      onNewChat={() => void nav('/chat')}
      activeChunkId={citation?.chunk_id}
      onOpenCitation={openCitation}
    />
  )
  const chat = docIntelEnabled() ? (
    <div className="flex min-h-0 min-w-0 flex-col">
      <IntelNav chatPath={sid ? `/chat/${encodeURIComponent(sid)}` : '/chat'} />
      <div className={intel ? 'hidden' : 'flex min-h-0 flex-1 flex-col [&>section]:flex-1'}>
        {chatPane}
      </div>
      {intel && <DocIntelView key={intel} kind={intel} onOpenCitation={openCitation} />}
    </div>
  ) : (
    chatPane
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
  const intel = docIntelEnabled()
    ? (['summary', 'sentiment', 'compare'] as const).find((k) => pathname === `/${k}`)
    : undefined
  return (
    <ScopeProvider sessionId={sessionId}>
      <Workspace
        sessionId={sessionId}
        intel={intel}
        initialView={pathname.startsWith('/documents') ? 'docs' : 'chat'}
      />
    </ScopeProvider>
  )
}
