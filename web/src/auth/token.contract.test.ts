import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { assertSchema, openapi } from '../test/openapiValidate'
import { ApiError } from '../api/errors'
import { refreshToken } from './token'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { MINT_ROUTE } from '../mocks/handlers'

const backend = (f: string) =>
  readFileSync(join(process.cwd(), '../src/backend/app/api', f), 'utf8')

describe('token flow matches the backend (T-034)', () => {
  it('backend 401 codes are token_expired (expired) vs invalid_token (bad) and the client re-mints on both', () => {
    const deps = backend('deps.py')
    expect(deps).toMatch(/except TokenExpired:\s+raise ApiError\(\s*401, "token_expired"/)
    expect(deps).toMatch(/except AuthError:\s+raise ApiError\(\s*401, "invalid_token"/)
    const client = readFileSync(join(process.cwd(), 'src/api/client.ts'), 'utf8')
    expect(client).toMatch(/new Set\(\['token_expired', 'invalid_token'\]\)/)
  })

  it('mint route, response shape and 429 code', () => {
    const anon = backend('auth_anon.py')
    expect(anon).toContain('router.post("/anonymous"')
    expect(anon).toContain('ApiError(429, "rate_limited"') // client maps any mint 429 to mint_rate_limited
    expect(Object.keys(openapi.paths)).toContain('/v1/auth/anonymous')
    const t = openapi.components.schemas.AnonymousToken as { required: string[] }
    expect(t.required).toEqual(['access_token', 'expires_at'])
    expect(MINT_ROUTE).toBe('*/v1/auth/anonymous')
  })

  it('the mock mint response validates and the client stores expires_at in unix seconds', async () => {
    const res = await fetch(`${location.origin}/v1/auth/anonymous`, { method: 'POST' })
    const body = (await res.json()) as { expires_at: number }
    assertSchema('AnonymousToken', body)
    expect(body.expires_at).toBeLessThan(10_000_000_000) // seconds, not ms
  })

  it('a backend-shaped mint 429 ({detail, code: rate_limited}) becomes mint_rate_limited', async () => {
    server.use(
      http.post(MINT_ROUTE, () =>
        HttpResponse.json(
          { detail: 'Too many tokens requested; retry later', code: 'rate_limited' },
          { status: 429, headers: { 'Retry-After': '60' } },
        ),
      ),
    )
    await expect(refreshToken()).rejects.toMatchObject({ status: 429, code: 'mint_rate_limited' })
    await expect(refreshToken()).rejects.toBeInstanceOf(ApiError)
  })
})
