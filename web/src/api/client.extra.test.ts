import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { MINT_ROUTE } from '../mocks/handlers'
import { server } from '../mocks/server'
import { multipart } from '../test/multipart'
import { api, unwrap } from './client'
import { GENERIC_MESSAGE, apiErrorFrom, messageFor } from './errors'

const body401 = (code: string) => HttpResponse.json({ detail: code, code }, { status: 401 })

function mintCounter() {
  const s = { mints: 0 }
  server.use(
    http.post(MINT_ROUTE, async () => {
      s.mints++
      await new Promise((r) => setTimeout(r, 5))
      return HttpResponse.json({
        access_token: `t${s.mints}`,
        token_type: 'bearer',
        expires_at: Math.floor(Date.now() / 1000) + 3600,
      })
    }),
  )
  return s
}

describe('api client (extra)', () => {
  it('401 token_expired: exactly one retry, with a new token', async () => {
    const s = mintCounter()
    const seen: string[] = []
    server.use(
      http.get('*/v1/documents', ({ request }) => {
        seen.push(request.headers.get('authorization') ?? '')
        return seen.length === 1
          ? body401('token_expired')
          : HttpResponse.json({ items: [], total: 0 })
      }),
    )
    unwrap(await api.GET('/v1/documents'))
    expect(seen).toEqual(['Bearer t1', 'Bearer t2'])
    expect(s.mints).toBe(2)
  })

  it('persistent 401 invalid_token: one re-mint, one retry, then auth_failed', async () => {
    const s = mintCounter()
    let calls = 0
    server.use(
      http.get('*/v1/documents', () => {
        calls++
        return body401('invalid_token')
      }),
    )
    await expect(api.GET('/v1/documents')).rejects.toMatchObject({ code: 'auth_failed' })
    expect(calls).toBe(2)
    expect(s.mints).toBe(2)
  })

  it('does not retry 401 with unknown code or a non-JSON body', async () => {
    const s = mintCounter()
    let calls = 0
    server.use(
      http.get('*/v1/documents', () => {
        calls++
        return new HttpResponse('<html>nope</html>', { status: 401 })
      }),
    )
    await api.GET('/v1/documents')
    expect(calls).toBe(1)
    expect(s.mints).toBe(1)
  })

  it('JSON body is intact on retry', async () => {
    mintCounter()
    const bodies: string[] = []
    server.use(
      http.post('*/v1/query', async ({ request }) => {
        bodies.push(await request.text())
        return bodies.length === 1
          ? body401('token_expired')
          : HttpResponse.json({ answer: 'a', citations: [], abstained: false })
      }),
    )
    await api.POST('/v1/query', { body: { question: 'why?', stream: false, top_k: 5 } })
    expect(bodies).toHaveLength(2)
    expect(bodies[1]).toBe(bodies[0])
    expect(JSON.parse(bodies[1])).toMatchObject({ question: 'why?' })
  })

  it('multipart body bytes are identical on retry', async () => {
    mintCounter()
    const texts: string[] = []
    server.use(
      http.post('*/v1/documents', async ({ request }) => {
        const f = (await request.formData()).getAll('files')[0] as File
        texts.push(await f.text())
        return texts.length === 1 ? body401('token_expired') : HttpResponse.json({ results: [] })
      }),
    )
    const mp = multipart([{ name: 'a.txt', content: 'payload-123' }])
    await api.POST('/v1/documents', {
      body: { files: [] },
      bodySerializer: () => mp.body,
      headers: mp.headers,
    })
    expect(texts).toEqual(['payload-123', 'payload-123'])
  })

  it('concurrent requests with no token share one mint', async () => {
    const s = mintCounter()
    server.use(http.get('*/v1/documents', () => HttpResponse.json({ items: [], total: 0 })))
    await Promise.all(Array.from({ length: 5 }, () => api.GET('/v1/documents')))
    expect(s.mints).toBe(1)
  })

  it('mint 429 surfaces as mint_rate_limited through the client', async () => {
    server.use(http.post(MINT_ROUTE, () => new HttpResponse(null, { status: 429 })))
    await expect(api.GET('/v1/documents')).rejects.toMatchObject({
      code: 'mint_rate_limited',
      status: 429,
    })
  })

  it('HTML error body is never echoed to the user message', async () => {
    mintCounter()
    server.use(
      http.get(
        '*/v1/documents',
        () =>
          new HttpResponse('<script>alert(1)</script>', {
            status: 500,
            headers: { 'Content-Type': 'text/html' },
          }),
      ),
    )
    const r = await api.GET('/v1/documents')
    let msg = ''
    try {
      unwrap(r)
    } catch (e) {
      msg = (e as Error).message
    }
    expect(msg).toBe(GENERIC_MESSAGE)
    expect(msg).not.toMatch(/[<>]/)
  })

  it('unknown code with hostile detail maps to the generic message', () => {
    const e = apiErrorFrom(500, { detail: '<img src=x onerror=1>', code: 'totally_new' })
    expect(e.message).toBe(GENERIC_MESSAGE)
    expect(messageFor('constructor')).toBe(GENERIC_MESSAGE)
    expect(messageFor('__proto__')).toBe(GENERIC_MESSAGE)
  })
})
