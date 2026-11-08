import { http, HttpResponse } from 'msw'
import type {
  AnonymousToken,
  ChunkPassage,
  DocumentList,
  DocumentOut,
  QueryRequest,
  QueryResponse,
  SessionCreated,
  SessionList,
  SessionListItem,
  SessionOut,
  UploadResponse,
  UploadResult,
} from '../api/contract'
import {
  chunkPassage,
  citation,
  initialDocuments,
  scenarios,
  sessionDetail,
  sessions,
} from './fixtures'
import { sseResponse } from './sse'

const err = (status: number, code: string, detail = code) =>
  HttpResponse.json({ detail, code }, { status })

/** Route pattern of the mint endpoint, shared with tests. */
export const MINT_ROUTE = '*/v1/auth/anonymous'

let documents: DocumentOut[] = initialDocuments()
let counter = 0
let sessionRows: SessionListItem[] = [...sessions]

/** Test helper: restore the in-memory mock state. */
export function resetMockState(): void {
  documents = initialDocuments()
  counter = 0
  sessionRows = [...sessions]
}

const fail = (filename: string, code: string): UploadResult => ({
  filename,
  ok: false,
  deduplicated: false,
  error: { code, detail: code },
})

const ALLOWED = /\.(pdf|docx|txt|csv|xlsx)$/i

export const handlers = [
  http.post(MINT_ROUTE, () => {
    const body: AnonymousToken = {
      access_token: `mock-token-${++counter}`,
      token_type: 'bearer',
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    }
    return HttpResponse.json(body)
  }),

  http.get('*/v1/documents', ({ request }) => {
    const url = new URL(request.url)
    const limit = Number(url.searchParams.get('limit') ?? 50)
    const offset = Number(url.searchParams.get('offset') ?? 0)
    const body: DocumentList = {
      items: documents.slice(offset, offset + limit),
      total: documents.length,
    }
    return HttpResponse.json(body)
  }),

  http.post('*/v1/documents', async ({ request }) => {
    const form = await request.formData()
    const files = form.getAll('files').filter((f): f is File => typeof f !== 'string')
    if (files.length === 0) return err(400, 'no_files')
    if (files.length > 10) return err(400, 'too_many_files')
    const results: UploadResult[] = files.map((f) => {
      if (!ALLOWED.test(f.name)) {
        return fail(f.name, 'bad_extension')
      }
      if (f.size === 0) {
        return fail(f.name, 'empty_file')
      }
      const doc: DocumentOut = {
        doc_id: `doc-new-${++counter}`,
        filename: f.name,
        mime: f.type || 'application/octet-stream',
        size: f.size,
        sha256: 'b'.repeat(64),
        pages_or_sheets: 1,
        status: 'ready',
        failure_reason: null,
        created_at: new Date().toISOString(),
      }
      documents = [doc, ...documents]
      return {
        filename: f.name,
        ok: true,
        doc_id: doc.doc_id,
        status: 'ready',
        deduplicated: false,
      }
    })
    const body: UploadResponse = { results }
    return HttpResponse.json(body)
  }),

  http.delete('*/v1/documents/:docId', ({ params }) => {
    const id = String(params.docId)
    if (!documents.some((d) => d.doc_id === id)) return err(404, 'not_found')
    documents = documents.filter((d) => d.doc_id !== id)
    return new HttpResponse(null, { status: 204 })
  }),

  http.get('*/v1/documents/:docId/chunks/:chunkId', ({ params }) => {
    if (!documents.some((d) => d.doc_id === params.docId)) return err(404, 'not_found')
    const body: ChunkPassage = {
      ...chunkPassage,
      doc_id: String(params.docId),
      chunk_id: String(params.chunkId),
    }
    return HttpResponse.json(body)
  }),

  http.get('*/v1/sessions', ({ request }) => {
    const url = new URL(request.url)
    const limit = Number(url.searchParams.get('limit') ?? 50)
    const offset = Number(url.searchParams.get('offset') ?? 0)
    const body: SessionList = {
      items: sessionRows.slice(offset, offset + limit),
      total: sessionRows.length,
    }
    return HttpResponse.json(body)
  }),

  http.post('*/v1/sessions', () => {
    const row: SessionListItem = {
      session_id: `sess-new-${++counter}`,
      title: 'New conversation',
      updated_at: new Date().toISOString(),
    }
    sessionRows = [row, ...sessionRows]
    const body: SessionCreated = { session_id: row.session_id }
    return HttpResponse.json(body, { status: 201 })
  }),

  http.get('*/v1/sessions/:sessionId', ({ params }) => {
    if (params.sessionId !== sessionDetail.session_id) return err(404, 'not_found')
    const body: SessionOut = sessionDetail
    return HttpResponse.json(body)
  }),

  http.post('*/v1/query', async ({ request }) => {
    const q = (await request.json()) as QueryRequest
    // Scenario keyword in the question picks the canned stream: abstain, max_steps, ...
    const key = Object.keys(scenarios).find(
      (k) => k !== 'done' && q.question.toLowerCase().includes(k),
    )
    const events = scenarios[key ?? 'done']
    if (q.stream) return sseResponse(events)
    const final = events[events.length - 1]
    if (final.event !== 'final') return err(500, 'internal')
    const body: QueryResponse = {
      answer: final.answer,
      citations: final.citations.length ? [citation] : [],
      abstained: final.abstained,
    }
    return HttpResponse.json(body)
  }),
]
