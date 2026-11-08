import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../../api/client'
import type { SessionCreated, SessionList, SessionOut } from '../../api/contract'

export const SESSIONS_KEY = ['sessions'] as const
export const PAGE_SIZE = 20

/** Paged conversation list ("load more" fetches the next offset). */
export function useSessions(limit = PAGE_SIZE) {
  return useInfiniteQuery({
    queryKey: SESSIONS_KEY,
    initialPageParam: 0,
    queryFn: async ({ pageParam }): Promise<SessionList> =>
      unwrap(await api.GET('/v1/sessions', { params: { query: { limit, offset: pageParam } } })),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0)
      return last.items.length > 0 && loaded < last.total ? loaded : undefined
    },
  })
}

/** One conversation with its messages; a 404 surfaces as an ApiError (not retried). */
export function useSession(sessionId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['session', sessionId],
    enabled: Boolean(sessionId) && enabled,
    retry: false,
    queryFn: async (): Promise<SessionOut> =>
      unwrap(
        await api.GET('/v1/sessions/{session_id}', {
          params: { path: { session_id: sessionId as string } },
        }),
      ),
  })
}

export function useCreateSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (): Promise<SessionCreated> => unwrap(await api.POST('/v1/sessions')),
    onSuccess: () => qc.invalidateQueries({ queryKey: SESSIONS_KEY }),
  })
}
