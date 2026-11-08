import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MINT_ROUTE } from '../mocks/handlers'
import { server } from '../mocks/server'
import { clearToken, getToken } from './token'

const now = () => Math.floor(Date.now() / 1000)
const SECRET = 'SECRET-TOKEN-VALUE-xyz'

function mintWith(expiresIn: number, status = 200) {
  const s = { calls: 0 }
  server.use(
    http.post(MINT_ROUTE, async () => {
      s.calls++
      await new Promise((r) => setTimeout(r, 5))
      if (status !== 200) {
        return HttpResponse.json({ detail: `leaked ${SECRET}`, code: 'internal' }, { status })
      }
      return HttpResponse.json({
        access_token: `${SECRET}-${s.calls}`,
        token_type: 'bearer',
        expires_at: now() + expiresIn,
      })
    }),
  )
  return s
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('token module (extra)', () => {
  it('never writes to or removes from localStorage', async () => {
    const calls = (['setItem', 'removeItem', 'clear'] as const).flatMap((m) => {
      const spy = vi.spyOn(Storage.prototype, m)
      return [{ m, spy }]
    })
    mintWith(3600)
    await getToken()
    clearToken()
    for (const { spy } of calls) {
      expect(spy.mock.contexts.filter((c) => c === window.localStorage)).toEqual([])
    }
    expect(calls.find((c) => c.m === 'setItem')!.spy.mock.contexts).toContain(window.sessionStorage)
    expect(localStorage.length).toBe(0)
  })

  it('token value never appears in console output', async () => {
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation(() => {}),
    )
    mintWith(3600)
    await getToken()
    mintWith(3600, 500)
    clearToken()
    await getToken().catch(() => {})
    const out = JSON.stringify(spies.flatMap((s) => s.mock.calls))
    expect(out).not.toContain(SECRET)
  })

  it('thrown errors never contain the token or raw server detail', async () => {
    mintWith(3600, 500)
    const err = await getToken().then(
      () => null,
      (e: Error) => e,
    )
    expect(err).toBeInstanceOf(Error)
    expect(`${err!.message} ${String(err)}`).not.toContain(SECRET)
  })

  it('10 concurrent getToken calls share a single mint', async () => {
    const s = mintWith(3600)
    const toks = await Promise.all(Array.from({ length: 10 }, () => getToken()))
    expect(s.calls).toBe(1)
    expect(new Set(toks).size).toBe(1)
  })

  it('pre-emptively refreshes once the clock enters the 30 s window (fake time)', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    const s = mintWith(120) // valid 120 s
    await getToken()
    vi.setSystemTime(new Date('2026-01-01T00:01:00Z')) // 60 s left: still valid
    await getToken()
    expect(s.calls).toBe(1)
    vi.setSystemTime(new Date('2026-01-01T00:01:31Z')) // 29 s left: refresh
    await getToken()
    expect(s.calls).toBe(2)
  })

  it('clearToken removes the stored value', async () => {
    mintWith(3600)
    await getToken()
    clearToken()
    expect(sessionStorage.length).toBe(0)
  })

  it('falls back to in-memory when sessionStorage throws', async () => {
    const boom = () => {
      throw new DOMException('denied', 'SecurityError')
    }
    vi.stubGlobal('sessionStorage', {
      getItem: boom,
      setItem: boom,
      removeItem: boom,
      clear: boom,
      key: boom,
      length: 0,
    })
    const s = mintWith(3600)
    const a = await getToken()
    const b = await getToken()
    expect(a).toBe(b)
    expect(s.calls).toBe(1)
    expect(() => clearToken()).not.toThrow()
  })
})
