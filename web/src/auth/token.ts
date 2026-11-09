/**
 * Anonymous token module (ADR-0004): the ONLY file that knows the storage key,
 * the mint endpoint and the auth header name. Token lives in sessionStorage only,
 * is never logged and never put in a URL.
 */
import { apiBase } from '../api/config'
import { ApiError, apiErrorFrom } from '../api/errors'

const STORAGE_KEY = 'docchat.token'
const MINT_PATH = '/v1/auth/anonymous'
const HEADER = 'Authorization'
const REFRESH_WINDOW_MS = 30_000

interface Stored {
  access_token: string
  expires_at: number
}

let inflight: Promise<string> | null = null
// Per-tab fallback used only while sessionStorage is unavailable (blocked/private mode).
let memory: Stored | null = null

function read(): Stored | null {
  let raw: string | null
  try {
    raw = sessionStorage.getItem(STORAGE_KEY)
  } catch {
    return memory
  }
  try {
    if (!raw) return memory
    const v: unknown = JSON.parse(raw)
    if (
      typeof v === 'object' &&
      v !== null &&
      typeof (v as Stored).access_token === 'string' &&
      typeof (v as Stored).expires_at === 'number'
    ) {
      return v as Stored
    }
  } catch {
    // corrupt value: treat as absent
  }
  return null
}

async function mint(): Promise<string> {
  let res: Response
  try {
    res = await fetch(`${apiBase()}${MINT_PATH}`, { method: 'POST' })
  } catch {
    throw new ApiError(0, 'network_error')
  }
  if (res.status === 429) throw new ApiError(429, 'mint_rate_limited')
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null)
    throw apiErrorFrom(res.status, body)
  }
  const body: unknown = await res.json().catch(() => null)
  const t = body as Partial<Stored> | null
  if (!t || typeof t.access_token !== 'string' || typeof t.expires_at !== 'number') {
    throw new ApiError(res.status, 'internal')
  }
  const stored: Stored = { access_token: t.access_token, expires_at: t.expires_at }
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(stored))
    memory = null
  } catch {
    memory = stored
  }
  return t.access_token
}

/** Mints a fresh token; concurrent callers share one request. */
export function refreshToken(): Promise<string> {
  inflight ??= mint().finally(() => {
    inflight = null
  })
  return inflight
}

/** Returns a valid token, minting when absent or within 30 s of expiry. */
export async function getToken(): Promise<string> {
  const s = read()
  if (s && s.expires_at * 1000 - Date.now() > REFRESH_WINDOW_MS) return s.access_token
  return refreshToken()
}

/**
 * Drops the stored token. With `failedHeader` (the `Bearer …` value that got a 401) it only
 * clears when that is still the stored token, so a concurrent re-mint is not discarded.
 */
export function clearToken(failedHeader?: string): void {
  if (failedHeader !== undefined) {
    const cur = read()
    if (cur && `Bearer ${cur.access_token}` !== failedHeader) return
  }
  memory = null
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // storage unavailable: nothing persisted
  }
}

/** Header carrying the bearer token, minting first if needed. */
export async function authHeaders(): Promise<Record<string, string>> {
  return { [HEADER]: `Bearer ${await getToken()}` }
}
