import createClient from 'openapi-fetch'
import { authHeaders, clearToken, refreshToken } from '../auth/token'
import type { paths } from './schema'
import { ApiError, apiErrorFrom, codeFromBody } from './errors'
import { apiBase } from './config'

const REMINT_CODES = new Set(['token_expired', 'invalid_token'])

async function send(req: Request): Promise<Response> {
  const attempt = req.clone()
  for (const [k, v] of Object.entries(await authHeaders())) attempt.headers.set(k, v)
  try {
    return await fetch(attempt)
  } catch {
    throw new ApiError(0, 'network_error')
  }
}

/**
 * fetch with bearer auth. A 401 `token_expired`/`invalid_token` re-mints and retries once;
 * a second 401 throws `auth_failed`. Safe to use for the SSE start (before the first byte only).
 */
export async function authedFetch(req: Request): Promise<Response> {
  const res = await send(req)
  if (res.status !== 401) return res
  const code = codeFromBody(
    await res
      .clone()
      .json()
      .catch(() => null),
  )
  if (!code || !REMINT_CODES.has(code)) return res
  clearToken()
  await refreshToken()
  const retry = await send(req)
  if (retry.status === 401) throw new ApiError(401, 'auth_failed')
  return retry
}

export const api = createClient<paths>({
  baseUrl: apiBase(),
  fetch: (req: Request) => authedFetch(req),
})

/** Unwraps an openapi-fetch result: returns data or throws ApiError. */
export function unwrap<T>(r: { data?: T; error?: unknown; response: Response }): T {
  if (r.error !== undefined || r.data === undefined) {
    if (r.response.ok) return undefined as T // 204 etc.
    throw apiErrorFrom(r.response.status, r.error)
  }
  return r.data
}
