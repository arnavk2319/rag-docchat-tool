import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { GENERIC_MESSAGE, messageFor } from './errors'

// Every `code` the backend can emit must have a specific message in errors.ts.
const APP = join(process.cwd(), '../src/backend/app')

function pyFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = join(dir, d.name)
    if (d.isDirectory()) return d.name === '__pycache__' ? [] : pyFiles(p)
    return d.name.endsWith('.py') ? [p] : []
  })
}
const text = pyFiles(APP)
  .map((p) => readFileSync(p, 'utf8'))
  .join('\n')

function backendCodes(): Set<string> {
  const out = new Set<string>()
  const add = (re: RegExp) => {
    for (const m of text.matchAll(re)) out.add(m[1])
  }
  add(/ApiError\(\s*\d+,\s*(?:code,\s*)?"([a-z_]+)"/g) // ApiError(status, "code", ...)
  add(/(?:UploadRejected|RequestTooLarge|UploadError)\(\s*(?:code=)?"([a-z_]+)"/g)
  add(/error_response\(\s*\d+,\s*"([a-z_]+)"/g)
  add(/ev\("error",\s*\{"code":\s*"([a-z_]+)"/g)
  // status -> code table in api/errors.py and the spend-cap code map in deps.py
  const table = text.match(/_STATUS_CODES = \{([^}]*)\}/)?.[1] ?? ''
  for (const m of table.matchAll(/:\s*"([a-z_]+)"/g)) out.add(m[1])
  const caps = text.match(/code = \{([^}]*)\}\.get\(/)?.[1] ?? ''
  for (const m of caps.matchAll(/:\s*"([a-z_]+)"/g)) out.add(m[1])
  out.add(text.match(/\.get\(\s*e\.scope,\s*"([a-z_]+)"/)?.[1] ?? 'spend_cap_exceeded_NOT_FOUND')
  // per-file failure reasons (_FAIL_DETAIL keys in api/uploads.py)
  const fd = text.match(/_FAIL_DETAIL[^=]*= \{([^}]*)\}/)?.[1] ?? ''
  for (const m of fd.matchAll(/"([a-z_]+)":/g)) out.add(m[1])
  out.delete('request_too_large') // internal; API answers 413 too_large
  out.delete('error') // fallback for statuses with no table entry
  return out
}

describe('backend error codes are mapped in errors.ts', () => {
  const codes = [...backendCodes()].sort()

  it('extraction found the known codes (guards the regexes)', () => {
    for (const c of [
      'not_found',
      'token_expired',
      'invalid_token',
      'unauthorized',
      'rate_limited',
      'global_rate_limited',
      'spend_cap_exceeded',
      'tenant_spend_cap_exceeded',
      'session_limit_exceeded',
      'budget_exceeded',
      'llm_unavailable',
      'llm_bad_output',
      'document_busy',
      'not_ready',
      'validation_error',
      'internal',
      'no_files',
      'too_many_files',
      'too_large',
      'bad_extension',
      'parse_error',
      'ingest_failed',
    ]) {
      expect(codes, c).toContain(c)
    }
  })

  it.each(codes)('%s has a specific message', (c) => {
    expect(messageFor(c), `unmapped backend code: ${c}`).not.toBe(
      c === 'internal' ? '' : GENERIC_MESSAGE,
    )
  })
})
