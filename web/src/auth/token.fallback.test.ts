import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearToken, getToken } from './token'

describe('token memory fallback', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    clearToken()
  })

  it('reuses the in-memory token when sessionStorage.setItem throws', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ access_token: 't1', expires_at: Math.floor(Date.now() / 1000) + 3600 }),
        ),
    )
    vi.stubGlobal('fetch', fetchMock)
    expect(await getToken()).toBe('t1')
    expect(await getToken()).toBe('t1')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
  })

  it('clearToken(failed) keeps a newer stored token', async () => {
    sessionStorage.setItem(
      'docchat.token',
      JSON.stringify({ access_token: 'new', expires_at: Math.floor(Date.now() / 1000) + 3600 }),
    )
    clearToken('Bearer old')
    expect(await getToken()).toBe('new')
  })
})
