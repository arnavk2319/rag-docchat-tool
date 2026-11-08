import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MINT_ROUTE } from '../mocks/handlers'
import { server } from '../mocks/server'
import { authHeaders, clearToken, getToken, refreshToken } from './token'

const MINT = MINT_ROUTE
const now = () => Math.floor(Date.now() / 1000)

function countingMint(expiresIn: number) {
  const state = { calls: 0 }
  server.use(
    http.post(MINT, async () => {
      state.calls++
      await new Promise((r) => setTimeout(r, 10))
      return HttpResponse.json({
        access_token: `tok-${state.calls}`,
        token_type: 'bearer',
        expires_at: now() + expiresIn,
      })
    }),
  )
  return state
}

afterEach(() => vi.restoreAllMocks())

describe('token module', () => {
  it('stores in sessionStorage only, never localStorage', async () => {
    countingMint(3600)
    await getToken()
    expect(sessionStorage.length).toBe(1)
    expect(sessionStorage.getItem(sessionStorage.key(0)!)).toContain('tok-1')
    expect(localStorage.length).toBe(0)
  })

  it('reuses a valid token', async () => {
    const s = countingMint(3600)
    await getToken()
    await getToken()
    expect(s.calls).toBe(1)
  })

  it('dedupes concurrent mints into one request', async () => {
    const s = countingMint(3600)
    const toks = await Promise.all([getToken(), getToken(), refreshToken(), getToken()])
    expect(s.calls).toBe(1)
    expect(new Set(toks).size).toBe(1)
  })

  it('refreshes when within 30 s of expiry', async () => {
    const s = countingMint(20)
    await getToken()
    await getToken()
    expect(s.calls).toBe(2)
  })

  it('does not refresh when just outside the 30 s window', async () => {
    const s = countingMint(60)
    await getToken()
    await getToken()
    expect(s.calls).toBe(1)
  })

  it('clearToken forces a new mint', async () => {
    const s = countingMint(3600)
    await getToken()
    clearToken()
    expect(sessionStorage.length).toBe(0)
    await getToken()
    expect(s.calls).toBe(2)
  })

  it('ignores a corrupt stored value', async () => {
    const s = countingMint(3600)
    await getToken()
    sessionStorage.setItem(sessionStorage.key(0)!, '{not json')
    await getToken()
    expect(s.calls).toBe(2)
  })

  it('maps mint 429 to the too-many-visitors error', async () => {
    server.use(
      http.post(MINT, () =>
        HttpResponse.json({ detail: 'x', code: 'rate_limited' }, { status: 429 }),
      ),
    )
    await expect(getToken()).rejects.toMatchObject({ code: 'mint_rate_limited', status: 429 })
    await expect(getToken()).rejects.toThrow(/too many visitors/i)
  })

  it('never logs the token', async () => {
    countingMint(3600)
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation(() => {}),
    )
    await getToken()
    await authHeaders()
    clearToken()
    for (const s of spies) expect(JSON.stringify(s.mock.calls)).not.toContain('tok-')
  })

  it('builds a bearer header', async () => {
    countingMint(3600)
    expect(Object.values(await authHeaders())).toEqual(['Bearer tok-1'])
  })
})
