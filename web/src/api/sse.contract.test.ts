import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { StreamEvent } from './contract'
import { assertSchema } from '../test/openapiValidate'
import { scenarios } from '../mocks/fixtures'

// Source of truth: docs/architecture/api.md "Agent SSE" table.
const doc = readFileSync(join(process.cwd(), '../docs/architecture/api.md'), 'utf8')
const section = doc.slice(doc.indexOf('## Agent SSE'))

const FIELDS: Record<StreamEvent['event'], string[]> = {
  step: ['node', 'n', 'summary'],
  tool_call: ['tool', 'args'],
  tool_result: ['tool', 'ok', 'n_items'],
  token: ['text'],
  citations: ['citations'],
  final: ['answer', 'citations', 'abstained', 'stopped_reason', 'usage'],
  error: ['code', 'detail'],
}
const NODES = ['router', 'plan', 'tool', 'grade', 'rewrite', 'answer', 'groundedness']
const REASONS = ['done', 'max_steps', 'max_tokens', 'max_usd', 'abstained', 'error']

describe('SSE union vs docs/architecture/api.md', () => {
  it('doc lists exactly the 7 events we model', () => {
    const rows = [...section.matchAll(/^\| `([a-z_]+)` \|/gm)].map((m) => m[1])
    expect(rows.sort()).toEqual(Object.keys(FIELDS).sort())
  })

  it('doc still names each modelled field, node and stopped_reason', () => {
    for (const [ev, fields] of Object.entries(FIELDS)) {
      const row = section.split('\n').find((l) => l.startsWith(`| \`${ev}\``)) ?? ''
      for (const f of fields) expect(row, `${ev}.${f}`).toContain(f)
    }
    for (const n of NODES) expect(section).toContain(n)
    for (const r of REASONS) expect(section).toContain(r)
    expect(section).toContain('span{char_start,char_end}')
  })

  it('every fixture event has the documented fields, run_id and seq', () => {
    const seen = new Set<string>()
    for (const evs of Object.values(scenarios)) {
      for (const e of evs) {
        seen.add(e.event)
        const rec = e as unknown as Record<string, unknown>
        expect(typeof rec.run_id).toBe('string')
        expect(Number.isInteger(rec.seq)).toBe(true)
        for (const f of FIELDS[e.event]) expect(rec, `${e.event}.${f}`).toHaveProperty(f)
        if (e.event === 'step') expect(NODES).toContain(e.node)
        if (e.event === 'final') {
          expect(REASONS).toContain(e.stopped_reason)
          expect(Object.keys(e.usage).sort()).toEqual(['steps', 'tokens', 'usd'])
        }
        if (e.event === 'citations' || e.event === 'final') {
          for (const c of e.citations) assertSchema('Citation', c)
        }
      }
    }
    expect([...seen].sort()).toEqual(Object.keys(FIELDS).sort())
  })
})
