import { expectTypeOf } from 'vitest'
import { describe, expect, it } from 'vitest'
import { api, unwrap } from '../api/client'
import type { components } from '../api/schema'
import type * as C from '../api/contract'
import { scenarios } from './fixtures'

type S = components['schemas']

describe('mock contract', () => {
  it('contract types are the generated schema types (compile-time)', () => {
    expectTypeOf<C.DocumentOut>().toEqualTypeOf<S['DocumentOut']>()
    expectTypeOf<C.DocumentList>().toEqualTypeOf<S['DocumentList']>()
    expectTypeOf<C.UploadResponse>().toEqualTypeOf<S['UploadResponse']>()
    expectTypeOf<C.SessionList>().toEqualTypeOf<S['SessionList']>()
    expectTypeOf<C.SessionOut>().toEqualTypeOf<S['SessionOut']>()
    expectTypeOf<C.ChunkPassage>().toEqualTypeOf<S['ChunkPassage']>()
    expectTypeOf<C.QueryResponse>().toEqualTypeOf<S['QueryResponse']>()
    expectTypeOf<C.AnonymousToken>().toEqualTypeOf<S['AnonymousToken']>()
    expect(true).toBe(true)
  })

  it('typed client reads handler responses as schema types (compile-time)', async () => {
    const docs = unwrap(await api.GET('/v1/documents'))
    expectTypeOf(docs).toEqualTypeOf<S['DocumentList']>()
    expect(Array.isArray(docs.items)).toBe(true)
  })

  it('error bodies carry {detail, code}', async () => {
    const cases: Array<[Response, number, string]> = [
      [
        await fetch(`${location.origin}/v1/documents/nope`, {
          method: 'DELETE',
          headers: { Authorization: 'Bearer x' },
        }),
        404,
        'not_found',
      ],
      [
        await fetch(`${location.origin}/v1/sessions/nope`, {
          headers: { Authorization: 'Bearer x' },
        }),
        404,
        'not_found',
      ],
      [
        await fetch(`${location.origin}/v1/documents/nope/chunks/c`, {
          headers: { Authorization: 'Bearer x' },
        }),
        404,
        'not_found',
      ],
      [
        await fetch(`${location.origin}/v1/documents`, { method: 'POST', body: new FormData() }),
        400,
        'no_files',
      ],
    ]
    for (const [res, status, code] of cases) {
      expect(res.status).toBe(status)
      const b = (await res.json()) as Record<string, unknown>
      expect(b).toEqual({ detail: expect.any(String), code })
    }
  })
})

describe('stream fixtures', () => {
  const reasons = ['done', 'abstained', 'max_steps', 'max_tokens', 'max_usd', 'error']

  it('cover every stopped_reason', () => {
    const got = Object.values(scenarios).map((evs) => {
      const f = evs[evs.length - 1]
      return f.event === 'final' ? f.stopped_reason : null
    })
    expect([...got].sort()).toEqual([...reasons].sort())
  })

  it.each(Object.entries(scenarios))(
    '%s ends with exactly one final and has ordered seq',
    (_k, evs) => {
      expect(evs[evs.length - 1].event).toBe('final')
      expect(evs.filter((e) => e.event === 'final')).toHaveLength(1)
      evs.forEach((e, i) => {
        expect(e.seq).toBe(i + 1)
        expect(e.run_id).toBeTruthy()
      })
    },
  )

  it('abstained scenario abstains with no citations; error scenario emits an error event', () => {
    const a = scenarios.abstained.at(-1)
    expect(a).toMatchObject({ abstained: true, citations: [] })
    expect(scenarios.error.some((e) => e.event === 'error')).toBe(true)
  })
})
