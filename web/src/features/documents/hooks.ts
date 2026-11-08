import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../../api/client'
import type { DocumentList, UploadResponse } from '../../api/contract'
import { ApiError, apiErrorFrom, messageFor } from '../../api/errors'
import type { FileProblem } from './validate'

export const DOCS_KEY = ['documents'] as const
const BUSY = new Set(['pending', 'processing'])

/** Lists documents; refetches every `pollMs` while any document is still being indexed. */
export function useDocuments(pollMs = 2000) {
  return useQuery({
    queryKey: DOCS_KEY,
    queryFn: async (): Promise<DocumentList> =>
      unwrap(await api.GET('/v1/documents', { params: { query: { limit: 200 } } })),
    refetchInterval: (q) => (q.state.data?.items.some((d) => BUSY.has(d.status)) ? pollMs : false),
  })
}

export interface UploadOutcome {
  problems: FileProblem[]
  duplicates: string[]
}

export function useUploadDocuments() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (files: File[]): Promise<UploadOutcome> => {
      const r = await api.POST('/v1/documents', {
        body: { files: [] },
        bodySerializer: () => {
          const fd = new FormData()
          for (const f of files) fd.append('files', f)
          return fd
        },
      })
      // All-failed batches come back 413/415 with the per-file `{results}` body.
      const payload: unknown = r.data ?? r.error
      const results = (payload as Partial<UploadResponse> | undefined)?.results
      if (!Array.isArray(results)) throw apiErrorFrom(r.response.status, r.error)
      const problems: FileProblem[] = []
      const duplicates: string[] = []
      results.forEach((res, i) => {
        if (res.ok && res.deduplicated) duplicates.push(res.filename || files[i]?.name || 'file')
        if (!res.ok) {
          const name = res.filename || files[i]?.name || 'file'
          problems.push({ filename: name, message: messageFor(res.error?.code) })
        }
      })
      return { problems, duplicates }
    },
    onSettled: () => qc.invalidateQueries({ queryKey: DOCS_KEY }),
  })
}

export function useRemoveDocument() {
  const qc = useQueryClient()
  return useMutation<void, ApiError, string, { prev?: DocumentList }>({
    mutationFn: async (id) => {
      unwrap(await api.DELETE('/v1/documents/{doc_id}', { params: { path: { doc_id: id } } }))
    },
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: DOCS_KEY })
      const prev = qc.getQueryData<DocumentList>(DOCS_KEY)
      if (prev) {
        qc.setQueryData<DocumentList>(DOCS_KEY, {
          items: prev.items.filter((d) => d.doc_id !== id),
          total: Math.max(0, prev.total - 1),
        })
      }
      return { prev }
    },
    onError: (_e, _id, ctx) => {
      if (ctx?.prev) qc.setQueryData(DOCS_KEY, ctx.prev)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: DOCS_KEY }),
  })
}

/** Alias matching the ticket's name. */
export const useDeleteDocument = useRemoveDocument
