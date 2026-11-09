import { api, unwrap } from '../../api/client'
import type { CompareResponse, DocSentiment, DocSummary } from './types'

export const fetchSummary = async (docId: string): Promise<DocSummary> =>
  unwrap(await api.POST('/v1/documents/{doc_id}/summary', { params: { path: { doc_id: docId } } }))

export const fetchSentiment = async (docId: string): Promise<DocSentiment> =>
  unwrap(
    await api.POST('/v1/documents/{doc_id}/sentiment', { params: { path: { doc_id: docId } } }),
  )

export const fetchCompare = async (docIds: string[], focus: string): Promise<CompareResponse> =>
  unwrap(await api.POST('/v1/compare', { body: { doc_ids: docIds, focus } }))
