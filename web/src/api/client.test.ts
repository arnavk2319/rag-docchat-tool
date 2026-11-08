import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { MINT_ROUTE } from '../mocks/handlers'
import { server } from '../mocks/server'
import { api, unwrap } from './client'
import { multipart } from '../test/multipart'
import { ApiError } from './errors'

const body401 = (code: string) => HttpResponse.json({ detail: code, code }, { status: 401 })

function mintCounter() {
  const s = { mints: 0 }
  server.use(
    http.post(MINT_ROUTE, () => {
      s.mints++
      return HttpResponse.json({
        access_token: `t${s.mints}`,
        token_type: 'bearer',
        expires_at: Math.floor(Date.now() / 1000) + 3600,
      })
    }),
  )
  return s
}

describe('api client', () => {
  it('sends the bearer token and returns typed data', async () => {
    let auth = ''
    server.use(
      http.get('*/v1/documents', ({ request }) => {
        auth = request.headers.get('authorization') ?? ''
        return HttpResponse.json({ items: [], total: 0 })
      }),
    )
    const r = unwrap(await api.GET('/v1/documents'))
    expect(r.total).toBe(0)
    expect(auth).toMatch(/^Bearer mock-token-/)
  })

  it.each(['token_expired', 'invalid_token'])(
    're-mints once and retries on 401 %s',
    async (code) => {
      const s = mintCounter()
      let calls = 0
      server.use(
        http.get('*/v1/documents', ({ request }) => {
          calls++
          return request.headers.get('authorization') === 'Bearer t1' && calls === 1
            ? body401(code)
            : HttpResponse.json({ items: [], total: 0 })
        }),
      )
      // first request mints t1; server rejects it; client mints t2 and retries
      unwrap(await api.GET('/v1/documents'))
      expect(calls).toBe(2)
      expect(s.mints).toBe(2)
    },
  )

  it('surfaces auth_failed on a second 401', async () => {
    const s = mintCounter()
    server.use(http.get('*/v1/documents', () => body401('token_expired')))
    await expect(api.GET('/v1/documents')).rejects.toMatchObject({
      code: 'auth_failed',
      status: 401,
    })
    expect(s.mints).toBe(2)
  })

  it('does not re-mint on other 401 codes', async () => {
    const s = mintCounter()
    server.use(http.get('*/v1/documents', () => body401('auth_required')))
    const r = await api.GET('/v1/documents')
    expect(s.mints).toBe(1)
    expect(() => unwrap(r)).toThrow(expect.objectContaining({ code: 'auth_required' }))
  })

  it('retries a request with a body (upload) after re-mint', async () => {
    mintCounter()
    let n = 0
    let names: string[] = []
    server.use(
      http.post('*/v1/documents', async ({ request }) => {
        if (++n === 1) return body401('token_expired')
        const f = await request.formData()
        names = f.getAll('files').map((x) => (x as File).name)
        return HttpResponse.json({ results: [] })
      }),
    )
    const mp = multipart([{ name: 'a.txt', content: 'hello' }])
    await api.POST('/v1/documents', {
      body: { files: [] },
      bodySerializer: () => mp.body,
      headers: mp.headers,
    })
    expect(n).toBe(2)
    expect(names).toEqual(['a.txt'])
  })

  it('maps 429 error bodies to ApiError', async () => {
    server.use(
      http.get('*/v1/documents', () =>
        HttpResponse.json({ detail: 'x', code: 'spend_cap_exceeded' }, { status: 429 }),
      ),
    )
    try {
      unwrap(await api.GET('/v1/documents'))
      expect.unreachable()
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError)
      expect((e as ApiError).code).toBe('spend_cap_exceeded')
    }
  })

  it('maps network failure to network_error', async () => {
    server.use(http.get('*/v1/documents', () => HttpResponse.error()))
    await expect(api.GET('/v1/documents')).rejects.toMatchObject({ code: 'network_error' })
  })
})
