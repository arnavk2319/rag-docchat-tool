import { describe, expect, it } from 'vitest'
import { expectTypeOf } from 'vitest'
import type { components } from '../api/schema'
import type * as C from '../api/contract'
import { assertSchema, openapi, validate } from '../test/openapiValidate'
import { multipart } from '../test/multipart'
import { chunkPassage, citation, initialDocuments, sessionDetail, sessions } from './fixtures'
import { MINT_ROUTE } from './handlers'

// Runtime contract-drift check: every mock response is validated against docs/openapi.json.
// Compile-time: handlers.ts and fixtures.ts are typed with the generated schema types, so
// `npm run typecheck` fails when the generated types change shape.

const H = { Authorization: 'Bearer x' }
const o = location.origin
const get = (p: string) => fetch(`${o}${p}`, { headers: H })
const json = async (r: Response) => (await r.json()) as unknown

describe('mock responses match docs/openapi.json (runtime)', () => {
  it('validator catches drift (guards the validator)', () => {
    expect(() => assertSchema('Citation', { ...citation, span: 'doc-level' })).toThrow(
      /Contract drift/,
    )
    expect(() => assertSchema('Citation', { doc_id: 'd' })).toThrow(
      /missing required field "filename"/,
    )
    expect(() =>
      assertSchema('DocumentOut', { ...initialDocuments()[0], status: 'weird' }),
    ).toThrow(/enum/)
    expect(() => assertSchema('ErrorBody', { detail: 'x' })).toThrow(/"code"/)
  })

  it('token mint', async () => {
    const res = await fetch(`${o}${MINT_ROUTE.replace('*', '')}`, { method: 'POST' })
    expect(res.status).toBe(200)
    const b = await json(res)
    assertSchema('AnonymousToken', b)
    expect((b as C.AnonymousToken).token_type).toBe('bearer')
  })

  it('documents list, upload (ok and per-file failure), chunk passage', async () => {
    assertSchema('DocumentList', await json(await get('/v1/documents')))
    const m = multipart([
      { name: 'a.txt', content: 'hello' },
      { name: 'b.exe', content: 'x' },
      { name: 'c.txt', content: '' },
    ])
    const up = await fetch(`${o}/v1/documents`, {
      method: 'POST',
      headers: { ...H, ...m.headers },
      body: m.body,
    })
    const body = (await json(up)) as C.UploadResponse
    assertSchema('UploadResponse', body)
    expect(body.results.map((r) => r.error?.code ?? 'ok')).toEqual([
      'ok',
      'bad_extension',
      'empty_file',
    ])
    assertSchema('ChunkPassage', await json(await get('/v1/documents/doc-1/chunks/doc-1:1:0')))
  })

  it('sessions list, create, get', async () => {
    assertSchema('SessionList', await json(await get('/v1/sessions')))
    const c = await fetch(`${o}/v1/sessions`, { method: 'POST', headers: H })
    expect(c.status).toBe(201)
    assertSchema('SessionCreated', await json(c))
    assertSchema('SessionOut', await json(await get('/v1/sessions/sess-1')))
  })

  it('doc-intel: summary, sentiment, compare and their error bodies', async () => {
    const post = (p: string, body?: object) =>
      fetch(`${o}${p}`, {
        method: 'POST',
        headers: { ...H, 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
    assertSchema('DocSummaryResponse', await json(await post('/v1/documents/doc-1/summary')))
    assertSchema('DocSentimentResponse', await json(await post('/v1/documents/doc-1/sentiment')))
    const up = multipart([{ name: 'b.txt', content: 'hello' }])
    await fetch(`${o}/v1/documents`, {
      method: 'POST',
      headers: { ...H, ...up.headers },
      body: up.body,
    })
    const ids = ((await json(await get('/v1/documents'))) as C.DocumentList).items
      .slice(0, 2)
      .map((d) => d.doc_id)
    assertSchema('CompareResponse', await json(await post('/v1/compare', { doc_ids: ids })))
    for (const r of [
      await post('/v1/documents/nope/summary'),
      await post('/v1/documents/nope/sentiment'),
      await post('/v1/compare', { doc_ids: ['nope', 'x'] }),
      await post('/v1/compare', { doc_ids: ['a'] }),
    ]) {
      expect(r.ok).toBe(false)
      assertSchema('ErrorBody', await json(r))
    }
  })

  it('non-stream query, abstained and answered', async () => {
    for (const question of ['revenue?', 'please abstained']) {
      const r = await fetch(`${o}/v1/query`, {
        method: 'POST',
        headers: { ...H, 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })
      assertSchema('QueryResponse', await json(r))
    }
  })

  it('error bodies satisfy ErrorBody ({detail, code})', async () => {
    for (const p of ['/v1/sessions/nope', '/v1/documents/nope/chunks/c']) {
      const r = await get(p)
      expect(r.status).toBe(404)
      assertSchema('ErrorBody', await json(r))
    }
  })

  it('static fixtures', () => {
    for (const d of initialDocuments()) assertSchema('DocumentOut', d)
    for (const s of sessions) assertSchema('SessionListItem', s)
    assertSchema('SessionOut', sessionDetail)
    assertSchema('ChunkPassage', chunkPassage)
    assertSchema('Citation', citation)
    expect(citation.span).toEqual({ char_start: 0, char_end: 42 })
  })

  it('enums come from the schema (document status, upload result, query)', () => {
    const doc = openapi.components.schemas.DocumentOut as {
      properties: { status: { enum: string[] } }
    }
    expect(doc.properties.status.enum).toEqual(['pending', 'processing', 'ready', 'failed'])
    expect(validate({ $ref: '#/components/schemas/Span' }, { char_start: 1 })).toHaveLength(1)
  })
})

describe('compile-time: contract.ts aliases are the generated types', () => {
  it('matches', () => {
    type S = components['schemas']
    expectTypeOf<C.Citation>().toEqualTypeOf<S['Citation']>()
    expectTypeOf<C.Span>().toEqualTypeOf<{ char_start: number; char_end: number }>()
    expectTypeOf<C.ErrorBody>().toEqualTypeOf<{ detail: string; code: string }>()
    expectTypeOf<C.DocumentOut['status']>().toEqualTypeOf<
      'pending' | 'processing' | 'ready' | 'failed'
    >()
    expect(true).toBe(true)
  })
})
