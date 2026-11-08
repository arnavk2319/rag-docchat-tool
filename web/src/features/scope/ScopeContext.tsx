/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useDocuments } from '../documents/hooks'
import { loadScope, saveScope } from './storage'

export interface ScopeDoc {
  id: string
  name: string
  ready: boolean
}

export interface Scope {
  selected: string[]
  docs: ScopeDoc[]
  /** Display name for a doc id, if known. */
  nameOf: (id: string) => string | undefined
  toggle: (id: string) => void
  selectAll: (ids: string[]) => void
  clear: () => void
}

const Ctx = createContext<Scope | null>(null)

export function useScope(): Scope {
  const v = useContext(Ctx)
  if (!v) throw new Error('useScope outside ScopeProvider')
  return v
}

/**
 * Holds the ticked document ids. With a `sessionId` the ticks are persisted per conversation and
 * restored when switching to it (T-042 hooks in via the same prop). Also keeps a name lookup.
 */
export function ScopeProvider({
  sessionId,
  children,
}: {
  sessionId?: string
  children: ReactNode
}) {
  const [rawSelected, setSelected] = useState<string[]>(
    () => (sessionId ? loadScope(sessionId) : null) ?? [],
  )
  const [seenSession, setSeenSession] = useState(sessionId)

  if (sessionId !== seenSession) {
    setSeenSession(sessionId)
    const stored = sessionId ? loadScope(sessionId) : null
    if (stored) setSelected(stored)
  }

  const list = useDocuments()
  // Only READY, still-existing documents may be sent as doc_ids (unknown -> 404, not ready -> 409).
  // Pruned only once a list has loaded, so a loading/failed list call never wipes the selection.
  const selected = useMemo(() => {
    const items = list.data?.items
    if (!items) return rawSelected
    const ready = new Set(items.filter((d) => d.status === 'ready').map((d) => d.doc_id))
    return rawSelected.filter((id) => ready.has(id))
  }, [rawSelected, list.data])

  useEffect(() => {
    if (sessionId) saveScope(sessionId, selected)
  }, [sessionId, selected])

  const docs = useMemo<ScopeDoc[]>(
    () =>
      (list.data?.items ?? []).map((d) => ({
        id: d.doc_id,
        name: d.filename,
        ready: d.status === 'ready',
      })),
    [list.data],
  )

  const toggle = useCallback(
    (id: string) =>
      setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].sort())),
    [],
  )
  const selectAll = useCallback((ids: string[]) => setSelected([...ids].sort()), [])
  const clear = useCallback(() => setSelected([]), [])

  const value = useMemo<Scope>(
    () => ({
      selected,
      docs,
      nameOf: (id) => docs.find((d) => d.id === id)?.name,
      toggle,
      selectAll,
      clear,
    }),
    [selected, docs, toggle, selectAll, clear],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
