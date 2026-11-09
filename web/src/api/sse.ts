import { authedFetch } from './client'
import { apiBase } from './config'
import type { QueryRequest, StreamEvent, StreamEventName } from './contract'
import { ApiError, apiErrorFrom } from './errors'

/** Hand-rolled SSE parser + streaming query (ADR-0004). Text in, validated events out. */

export interface SseFrame {
  event: string
  data: string
}

/**
 * Incremental frame parser. Feed bytes or strings in any split; it handles CRLF/CR/LF line ends,
 * multi-line `data`, `:` comments and UTF-8 sequences cut across chunks. A frame is emitted only
 * at its terminating blank line, so a truncated trailing frame is dropped.
 */
export class SseFrameParser {
  private decoder = new TextDecoder('utf-8')
  private buf = ''
  private event = ''
  private data: string[] = []

  push(chunk: Uint8Array | string): SseFrame[] {
    this.buf += typeof chunk === 'string' ? chunk : this.decoder.decode(chunk, { stream: true })
    return this.drain(false)
  }

  /** Call at end of input. */
  end(): SseFrame[] {
    this.buf += this.decoder.decode()
    return this.drain(true)
  }

  private drain(final: boolean): SseFrame[] {
    // A trailing CR may be the first half of CRLF: hold it until the next chunk.
    let hold = ''
    if (!final && this.buf.endsWith('\r')) {
      hold = '\r'
      this.buf = this.buf.slice(0, -1)
    }
    const lines = this.buf.replace(/\r\n?/g, '\n').split('\n')
    this.buf = (lines.pop() ?? '') + hold
    if (final && this.buf) {
      lines.push(this.buf) // unterminated last line: parsed, but no blank line follows
      this.buf = ''
    }
    const out: SseFrame[] = []
    for (const line of lines) {
      if (line === '') {
        if (this.data.length > 0)
          out.push({ event: this.event || 'message', data: this.data.join('\n') })
        this.event = ''
        this.data = []
        continue
      }
      if (line.startsWith(':')) continue
      const i = line.indexOf(':')
      const name = i === -1 ? line : line.slice(0, i)
      let value = i === -1 ? '' : line.slice(i + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (name === 'event') this.event = value
      else if (name === 'data') this.data.push(value)
    }
    return out
  }
}

const NAMES: ReadonlySet<string> = new Set<StreamEventName>([
  'step',
  'tool_call',
  'tool_result',
  'token',
  'citations',
  'final',
  'error',
])

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

/** Frame -> typed event, or null for unknown/malformed frames (ignored). */
export function toEvent(frame: SseFrame): StreamEvent | null {
  if (!NAMES.has(frame.event)) return null
  let p: unknown
  try {
    p = JSON.parse(frame.data)
  } catch {
    return null
  }
  if (!isObj(p)) return null
  const ok =
    (frame.event === 'token' && typeof p.text === 'string') ||
    (frame.event === 'final' &&
      typeof p.answer === 'string' &&
      typeof p.stopped_reason === 'string' &&
      Array.isArray(p.citations) &&
      isObj(p.usage)) ||
    (frame.event === 'citations' && Array.isArray(p.citations)) ||
    (frame.event === 'error' && typeof p.code === 'string') ||
    (frame.event === 'step' && typeof p.summary === 'string') ||
    (frame.event === 'tool_call' && typeof p.tool === 'string') ||
    (frame.event === 'tool_result' && typeof p.tool === 'string')
  return ok ? ({ ...p, event: frame.event } as unknown as StreamEvent) : null
}

/** Raised when the stream closes before a `final` event (not when the caller aborted). */
export class StreamIncompleteError extends Error {
  constructor() {
    super('The response ended unexpectedly. Please try again.')
    this.name = 'StreamIncompleteError'
  }
}

/** Parses a byte stream into events. Stops (without error) when `signal` aborts. */
export async function* parseEventStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const reader = body.getReader()
  const onAbort = () => void reader.cancel().catch(() => undefined)
  signal?.addEventListener('abort', onAbort)
  const parser = new SseFrameParser()
  let sawFinal = false
  try {
    for (;;) {
      if (signal?.aborted) return
      let r: ReadableStreamReadResult<Uint8Array>
      try {
        r = await reader.read()
      } catch (e) {
        if (signal?.aborted) return
        throw e
      }
      const frames = r.done ? parser.end() : parser.push(r.value)
      for (const f of frames) {
        const ev = toEvent(f)
        if (!ev) continue
        if (ev.event === 'final') sawFinal = true
        yield ev
        if (sawFinal) return
      }
      if (r.done) break
    }
    if (!sawFinal && !signal?.aborted) throw new StreamIncompleteError()
  } finally {
    signal?.removeEventListener('abort', onAbort)
    void reader.cancel().catch(() => undefined)
  }
}

/**
 * POST /v1/query with stream=true. Auth + the single 401 retry happen in `authedFetch` before the
 * first byte; once bytes arrive nothing is replayed. Pre-stream HTTP errors throw `ApiError`.
 */
export async function* streamQuery(
  q: Omit<QueryRequest, 'stream' | 'top_k'>,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const req = new Request(`${apiBase()}/v1/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ ...q, stream: true }),
    signal,
  })
  let res: Response
  try {
    res = await authedFetch(req)
  } catch (e) {
    if (signal?.aborted) return
    throw e
  }
  if (!res.ok) {
    throw apiErrorFrom(res.status, await res.json().catch(() => null))
  }
  if (!res.body) throw new ApiError(0, 'network_error')
  yield* parseEventStream(res.body, signal)
}
