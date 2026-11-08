import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { GENERIC_MESSAGE, messageFor } from './errors'

const src = readFileSync(join(process.cwd(), 'src/api/errors.ts'), 'utf8')
const block = src.slice(src.indexOf('const MESSAGES'), src.indexOf('export function messageFor'))
const codes = [...block.matchAll(/^\s{2}([a-z_]+):/gm)].map((m) => m[1])

describe('error code coverage', () => {
  it('found the codes in errors.ts (guards the regex)', () => {
    expect(codes.length).toBeGreaterThanOrEqual(20)
    for (const c of [
      'rate_limited',
      'spend_cap_exceeded',
      'budget_exceeded',
      'llm_unavailable',
      'document_busy',
      'auth_required',
      'not_ready',
      'mint_rate_limited',
      'auth_failed',
    ]) {
      expect(codes).toContain(c)
    }
  })

  it.each(codes)('%s has a non-empty plain-text message', (c) => {
    const m = messageFor(c)
    expect(m.trim().length).toBeGreaterThan(0)
    expect(m).not.toMatch(/[<>]/)
    expect(m).not.toContain(c) // no raw code leaked as the message
  })

  it('only generic-mapped codes share the generic message', () => {
    for (const c of codes) {
      if (messageFor(c) === GENERIC_MESSAGE) expect(c).toBe('internal')
    }
  })
})
