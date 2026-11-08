import { describe, expect, it } from 'vitest'
import { api, authedFetch, unwrap } from '../api/client'
import { multipart } from '../test/multipart'
import type { StreamEvent } from '../api/contract'

async function readEvents(question: string): Promise<StreamEvent[]> {
  const res = await authedFetch(
    new Request(`${window.location.origin}/v1/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, stream: true }),
    }),
  )
  const text = await res.text()
  return text
    .split('\n\n')
    .filter(Boolean)
    .map((frame) => {
      const [e, d] = frame.split('\n')
      return { event: e.slice(7), ...JSON.parse(d.slice(6)) } as StreamEvent
    })
}

describe('mock handlers', () => {
  it('lists, uploads and deletes documents', async () => {
    expect(unwrap(await api.GET('/v1/documents')).total).toBe(1)
    const mp = multipart([
      { name: 'n.txt', content: 'x' },
      { name: 'bad.exe', content: 'x' },
    ])
    const up = unwrap(
      await api.POST('/v1/documents', {
        body: { files: [] },
        bodySerializer: () => mp.body,
        headers: mp.headers,
      }),
    )
    expect(up.results.map((r) => r.ok)).toEqual([true, false])
    expect(up.results[1].error?.code).toBe('bad_extension')
    expect(unwrap(await api.GET('/v1/documents')).total).toBe(2)
    const del = await api.DELETE('/v1/documents/{doc_id}', {
      params: { path: { doc_id: 'doc-1' } },
    })
    expect(del.response.status).toBe(204)
    expect(unwrap(await api.GET('/v1/documents')).total).toBe(1)
  })

  it('serves sessions and the cited passage', async () => {
    expect(unwrap(await api.GET('/v1/sessions')).items).toHaveLength(1)
    const s = unwrap(
      await api.GET('/v1/sessions/{session_id}', { params: { path: { session_id: 'sess-1' } } }),
    )
    expect(s.messages).toHaveLength(2)
    const c = unwrap(
      await api.GET('/v1/documents/{doc_id}/chunks/{chunk_id}', {
        params: { path: { doc_id: 'doc-1', chunk_id: 'doc-1:1:0' } },
      }),
    )
    expect(c.highlight.end).toBeGreaterThan(c.highlight.start)
    expect(c.highlight.end).toBeLessThanOrEqual(c.text.length)
  })

  it.each([
    ['revenue?', 'done'],
    ['please abstained', 'abstained'],
    ['max_steps', 'max_steps'],
    ['max_tokens', 'max_tokens'],
    ['max_usd', 'max_usd'],
    ['trigger error', 'error'],
  ])('streams scenario for %s', async (q, reason) => {
    const evs = await readEvents(q)
    expect(evs.map((e) => e.seq)).toEqual(evs.map((_, i) => i + 1))
    const last = evs[evs.length - 1]
    expect(last.event).toBe('final')
    if (last.event === 'final') {
      expect(last.stopped_reason).toBe(reason)
      expect(last.abstained).toBe(reason === 'abstained')
    }
  })

  it('done stream has chunk-level citations with object spans', async () => {
    const evs = await readEvents('revenue?')
    const c = evs.find((e) => e.event === 'citations')
    expect(c?.event === 'citations' && c.citations[0].span).toEqual({ char_start: 0, char_end: 42 })
  })
})
