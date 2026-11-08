import { describe, expect, it } from 'vitest'
import { ApiError, GENERIC_MESSAGE, apiErrorFrom, messageFor } from './errors'

const CODES = [
  'rate_limited',
  'spend_cap_exceeded',
  'budget_exceeded',
  'llm_unavailable',
  'document_busy',
  'auth_required',
  'not_ready',
  'auth_failed',
  'mint_rate_limited',
  'no_files',
  'too_many_files',
  'bad_extension',
  'empty_file',
  'too_large',
  'content_mismatch',
  'bad_content_type',
  'parse_error',
  'empty_document',
  'ingest_failed',
]

describe('error mapping', () => {
  it.each(CODES)('maps %s to a specific text message', (code) => {
    const m = messageFor(code)
    expect(m.length).toBeGreaterThan(5)
    expect(m).not.toBe(GENERIC_MESSAGE)
  })

  it('maps unknown and missing codes to the generic message', () => {
    expect(messageFor('totally_new')).toBe(GENERIC_MESSAGE)
    expect(messageFor(undefined)).toBe(GENERIC_MESSAGE)
    expect(messageFor('toString')).toBe(GENERIC_MESSAGE)
  })

  it('builds ApiError from a body, never exposing raw detail', () => {
    const e = apiErrorFrom(429, { code: 'rate_limited', detail: '<img onerror=x>' })
    expect(e).toBeInstanceOf(ApiError)
    expect(e.message).not.toContain('<img')
    expect(apiErrorFrom(500, 'garbage').code).toBe('internal')
  })
})
