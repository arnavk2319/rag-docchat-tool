import type { Citation, StoppedReason, StreamEvent } from '../../api/contract'
import { messageFor } from '../../api/errors'
import { decodeEntities } from '../../api/text'

export interface TraceItem {
  kind: 'step' | 'tool_call' | 'tool_result'
  text: string
}

export type AssistantStatus = 'streaming' | 'done' | 'stopped' | 'failed'

export interface UserMsg {
  role: 'user'
  id: number
  text: string
}
export interface NoteMsg {
  role: 'note'
  id: number
  text: string
}
export interface AssistantMsg {
  role: 'assistant'
  id: number
  status: AssistantStatus
  trace: TraceItem[]
  answer: string
  citations: Citation[]
  stoppedReason?: StoppedReason
  abstained?: boolean
  usage?: { steps: number; tokens: number; usd: number }
  /** Mapped, user-facing error text (pre-stream error, `error` event, or truncated stream). */
  errorText?: string
}
export type Msg = UserMsg | NoteMsg | AssistantMsg

export interface ChatState {
  messages: Msg[]
  streaming: boolean
  nextId: number
}

export type Action =
  | { type: 'send'; question: string }
  | { type: 'event'; event: StreamEvent }
  | { type: 'stopped' }
  | { type: 'failed'; message: string }
  | { type: 'note'; text: string }

export const initialState: ChatState = { messages: [], streaming: false, nextId: 1 }

const dec = (c: Citation): Citation => ({
  ...c,
  filename: decodeEntities(c.filename),
  sheet: c.sheet == null ? c.sheet : decodeEntities(c.sheet),
})

function argsText(args: Record<string, unknown>): string {
  const s = Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(' ')
  return s ? ` ${decodeEntities(s)}` : ''
}

function traceFor(e: StreamEvent): TraceItem | null {
  switch (e.event) {
    case 'step':
      return { kind: 'step', text: `${e.node} · ${decodeEntities(e.summary)}` }
    case 'tool_call':
      return { kind: 'tool_call', text: `call ${decodeEntities(e.tool)}${argsText(e.args ?? {})}` }
    case 'tool_result':
      return {
        kind: 'tool_result',
        text: `${decodeEntities(e.tool)} ${e.ok ? 'ok' : 'failed'} · ${e.n_items} items`,
      }
    default:
      return null
  }
}

/** Applies `fn` to the last assistant message only while it is the live one. */
function updateLast(
  s: ChatState,
  fn: (m: AssistantMsg) => AssistantMsg,
  extra: Partial<ChatState> = {},
): ChatState {
  const i = s.messages.length - 1
  const m = s.messages[i]
  if (!m || m.role !== 'assistant') return s
  const messages = s.messages.slice()
  messages[i] = fn(m)
  return { ...s, ...extra, messages }
}

export function reducer(s: ChatState, a: Action): ChatState {
  switch (a.type) {
    case 'send': {
      const user: UserMsg = { role: 'user', id: s.nextId, text: a.question }
      const bot: AssistantMsg = {
        role: 'assistant',
        id: s.nextId + 1,
        status: 'streaming',
        trace: [],
        answer: '',
        citations: [],
      }
      return { messages: [...s.messages, user, bot], streaming: true, nextId: s.nextId + 2 }
    }
    case 'note':
      return {
        ...s,
        messages: [...s.messages, { role: 'note', id: s.nextId, text: a.text }],
        nextId: s.nextId + 1,
      }
    case 'stopped':
      if (!s.streaming) return s
      return updateLast(s, (m) => ({ ...m, status: 'stopped' }), { streaming: false })
    case 'failed':
      if (!s.streaming) return s
      return updateLast(s, (m) => ({ ...m, status: 'failed', errorText: a.message }), {
        streaming: false,
      })
    case 'event': {
      const e = a.event
      if (!s.streaming) return s
      const t = traceFor(e)
      if (t) return updateLast(s, (m) => ({ ...m, trace: [...m.trace, t] }))
      switch (e.event) {
        case 'token':
          return updateLast(s, (m) => ({ ...m, answer: m.answer + decodeEntities(e.text) }))
        case 'citations':
          return updateLast(s, (m) => ({ ...m, citations: e.citations.map(dec) }))
        case 'error':
          return updateLast(s, (m) => ({ ...m, errorText: messageFor(e.code) }))
        case 'final':
          return updateLast(
            s,
            (m) => ({
              ...m,
              status: 'done',
              answer: decodeEntities(e.answer),
              citations: e.citations.map(dec),
              abstained: e.abstained,
              stoppedReason: e.stopped_reason,
              usage: e.usage,
            }),
            { streaming: false },
          )
        default:
          return s
      }
    }
  }
}

export interface StopInfo {
  label: string
  tone: 'ok' | 'warn' | 'error'
}

/** One distinct, labelled message per stop reason. */
export function stopInfo(reason: StoppedReason): StopInfo {
  switch (reason) {
    case 'done':
      return { label: 'Completed', tone: 'ok' }
    case 'abstained':
      return {
        label: "I don't know: the selected documents don't answer this question",
        tone: 'warn',
      }
    case 'max_steps':
      return { label: 'Stopped: step limit reached before an answer was complete', tone: 'warn' }
    case 'max_tokens':
      return { label: 'Stopped: token budget reached before an answer was complete', tone: 'warn' }
    case 'max_usd':
      return { label: 'Stopped: cost budget reached before an answer was complete', tone: 'warn' }
    case 'error':
      return { label: 'Stopped: an error occurred while answering', tone: 'error' }
    default:
      return { label: 'Stopped: an error occurred while answering', tone: 'error' }
  }
}
