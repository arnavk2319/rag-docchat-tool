import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { MINT_ROUTE } from '../mocks/handlers'
import { server } from '../mocks/server'
import { ApiError } from './errors'
import {
  parseEventStream,
  SseFrameParser,
  StreamIncompleteError,
  streamQuery,
  toEvent,
} from './sse'
import type { StreamEvent } from './contract'

const enc = new TextEncoder()
const ev = (event: string, data: object) =>
  `event: ${event}\ndata: ${JSON.stringify({ run_id: 'r', seq: 1, ...data })}\n\n`
const FINAL = ev('final', {
  answer: 'ok',
  citations: [],
  abstained: false,
  stopped_reason: 'done',
  usage: { steps: 1, tokens: 1, usd: 0 },
})

function events(parser: SseFrameParser, chunks: Array<Uint8Array | string>): StreamEvent[] {
  const frames = [...chunks.flatMap((c) => parser.push(c)), ...parser.end()]
  return frames.map(toEvent).filter((e): e is StreamEvent => e !== null)
}

function bodyOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(c) {
      chunks.forEach((x) => c.enqueue(x))
      c.close()
    },
  })
}

async function collect(it: AsyncIterable<StreamEvent>): Promise<StreamEvent[]> {
  const out: StreamEvent[] = []
  for await (const e of it) out.push(e)
  return out
}

describe('SseFrameParser', () => {
  const text =
    ev('step', { node: 'plan', n: 1, summary: 'Café 日本語 🚀' }) +
    ev('token', { text: 'héllo' }) +
    FINAL
  const bytes = enc.encode(text)
  const whole = events(new SseFrameParser(), [bytes])

  it('parses the whole fixture', () => {
    expect(whole.map((e) => e.event)).toEqual(['step', 'token', 'final'])
    expect(whole[0]).toMatchObject({ summary: 'Café 日本語 🚀' })
  })

  it('gives identical events when split at every byte offset (incl. mid UTF-8)', () => {
    for (let i = 1; i < bytes.length; i++) {
      expect(events(new SseFrameParser(), [bytes.slice(0, i), bytes.slice(i)])).toEqual(whole)
    }
  })

  it('handles CRLF and lone CR, split at every offset', () => {
    const crlf = enc.encode(text.replace(/\n/g, '\r\n'))
    const cr = enc.encode(text.replace(/\n/g, '\r'))
    for (const b of [crlf, cr]) {
      for (let i = 1; i < b.length; i++) {
        expect(events(new SseFrameParser(), [b.slice(0, i), b.slice(i)])).toEqual(whole)
      }
    }
  })

  it('joins multi-line data with newlines and ignores comments', () => {
    const f = new SseFrameParser().push(': ping\nevent: token\ndata: {"text":\ndata: "a"}\n\n')
    expect(f).toEqual([{ event: 'token', data: '{"text":\n"a"}' }])
    expect(toEvent(f[0])).toMatchObject({ event: 'token', text: 'a' })
  })

  it('ignores unknown events and malformed payloads', () => {
    const got = events(new SseFrameParser(), [
      'event: mystery\ndata: {}\n\n',
      'event: token\ndata: not json\n\n',
      'event: token\ndata: {"nope":1}\n\n',
      ev('token', { text: 'x' }),
    ])
    expect(got).toHaveLength(1)
  })

  it('drops a truncated trailing frame', () => {
    expect(events(new SseFrameParser(), ['event: token\ndata: {"text":"x"}\n'])).toEqual([])
  })
})

describe('parseEventStream', () => {
  it('yields events and stops at final', async () => {
    const got = await collect(
      parseEventStream(bodyOf([enc.encode(ev('token', { text: 'a' }) + FINAL)])),
    )
    expect(got.map((e) => e.event)).toEqual(['token', 'final'])
  })

  it('stream ending without final is an error', async () => {
    await expect(
      collect(parseEventStream(bodyOf([enc.encode(ev('token', { text: 'a' }))]))),
    ).rejects.toBeInstanceOf(StreamIncompleteError)
  })
})

describe('streamQuery', () => {
  const q = { question: 'hi', doc_ids: ['d'] }

  it('posts stream=true with doc_ids and session_id', async () => {
    let body: unknown
    server.use(
      http.post('*/v1/query', async ({ request }) => {
        body = await request.json()
        return new HttpResponse(FINAL, { headers: { 'Content-Type': 'text/event-stream' } })
      }),
    )
    await collect(streamQuery({ ...q, session_id: 's1' }))
    expect(body).toMatchObject({ question: 'hi', doc_ids: ['d'], session_id: 's1', stream: true })
  })

  it('abort mid-stream cancels the reader and ends without error', async () => {
    let cancelled = false
    server.use(
      http.post('*/v1/query', () => {
        const body = new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(enc.encode(ev('token', { text: 'a' })))
          },
          cancel() {
            cancelled = true
          },
        })
        return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }),
    )
    const ac = new AbortController()
    const got: StreamEvent[] = []
    for await (const e of streamQuery(q, ac.signal)) {
      got.push(e)
      ac.abort()
    }
    expect(got).toHaveLength(1)
    expect(ac.signal.aborted).toBe(true)
    await new Promise((r) => setTimeout(r, 10))
    expect(cancelled).toBe(true)
  })

  it('retries once on 401 before the first byte', async () => {
    let posts = 0
    let mints = 0
    server.use(
      http.post(MINT_ROUTE, () => {
        mints++
        return HttpResponse.json({
          access_token: `t${mints}`,
          token_type: 'bearer',
          expires_at: Math.floor(Date.now() / 1000) + 3600,
        })
      }),
      http.post('*/v1/query', () => {
        posts++
        if (posts === 1) {
          return HttpResponse.json({ detail: 'x', code: 'token_expired' }, { status: 401 })
        }
        return new HttpResponse(FINAL, { headers: { 'Content-Type': 'text/event-stream' } })
      }),
    )
    const got = await collect(streamQuery(q))
    expect(posts).toBe(2)
    expect(got.at(-1)?.event).toBe('final')
  })

  it('does not replay after bytes were received', async () => {
    let posts = 0
    server.use(
      http.post('*/v1/query', () => {
        posts++
        const body = new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(enc.encode(ev('token', { text: 'a' })))
            c.close() // dropped before final
          },
        })
        return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }),
    )
    await expect(collect(streamQuery(q))).rejects.toBeInstanceOf(StreamIncompleteError)
    expect(posts).toBe(1)
  })

  it('throws a mapped ApiError for pre-stream errors', async () => {
    server.use(
      http.post('*/v1/query', () =>
        HttpResponse.json({ detail: 'x', code: 'rate_limited' }, { status: 429 }),
      ),
    )
    await expect(collect(streamQuery(q))).rejects.toMatchObject({
      constructor: ApiError,
      status: 429,
      code: 'rate_limited',
    })
  })
})
