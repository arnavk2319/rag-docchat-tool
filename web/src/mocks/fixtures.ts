import type {
  ChunkPassage,
  Citation,
  DocumentOut,
  SessionListItem,
  SessionOut,
  StreamEvent,
  StoppedReason,
} from '../api/contract'

export const MOCK_RUN_ID = 'run-mock-1'

export const citation: Citation = {
  doc_id: 'doc-1',
  filename: 'annual-report.pdf',
  chunk_id: 'doc-1:1:0',
  page: 1,
  sheet: null,
  span: { char_start: 0, char_end: 42 },
}

export const initialDocuments = (): DocumentOut[] => [
  {
    doc_id: 'doc-1',
    filename: 'annual-report.pdf',
    mime: 'application/pdf',
    size: 120_000,
    sha256: 'a'.repeat(64),
    pages_or_sheets: 12,
    status: 'ready',
    failure_reason: null,
    created_at: '2026-11-08T09:00:00Z',
  },
]

export const sessions: SessionListItem[] = [
  { session_id: 'sess-1', title: 'What was revenue?', updated_at: '2026-11-08T09:30:00Z' },
]

export const sessionDetail: SessionOut = {
  session_id: 'sess-1',
  messages: [
    {
      role: 'user',
      content: 'What was revenue?',
      citation_ids: [],
      created_at: '2026-11-08T09:29:00Z',
    },
    {
      role: 'assistant',
      content: 'Revenue was 4.2 million.',
      citation_ids: ['doc-1:1:0'],
      created_at: '2026-11-08T09:30:00Z',
    },
  ],
}

export const chunkPassage: ChunkPassage = {
  doc_id: 'doc-1',
  chunk_id: 'doc-1:1:0',
  filename: 'annual-report.pdf',
  page: 1,
  sheet: null,
  text: 'Revenue was 4.2 million in fiscal 2025, up 8 percent. Costs were flat.',
  highlight: { start: 0, end: 42 },
}

type Payload = StreamEvent extends infer E ? (E extends unknown ? Omit<E, 'seq'> : never) : never

const usage = { steps: 3, tokens: 900, usd: 0.0012 }

function stream(
  stopped: StoppedReason,
  answer: string,
  abstained: boolean,
  cites: Citation[],
): StreamEvent[] {
  const base = { run_id: MOCK_RUN_ID }
  const evs: Payload[] = [
    { ...base, event: 'step', node: 'router', n: 1, summary: 'Routing question' },
    { ...base, event: 'tool_call', tool: 'search', args: { query: 'revenue' } },
    { ...base, event: 'tool_result', tool: 'search', ok: true, n_items: abstained ? 0 : 3 },
    { ...base, event: 'step', node: 'answer', n: 2, summary: 'Writing answer' },
  ]
  if (answer && stopped === 'done') {
    for (const w of answer.split(' ')) evs.push({ ...base, event: 'token', text: `${w} ` })
  }
  if (cites.length) evs.push({ ...base, event: 'citations', citations: cites })
  if (stopped === 'error') {
    evs.push({
      ...base,
      event: 'error',
      code: 'llm_unavailable',
      detail: 'The language model is unavailable.',
    })
  }
  evs.push({
    ...base,
    event: 'final',
    answer,
    citations: cites,
    abstained,
    stopped_reason: stopped,
    usage,
  })
  return evs.map((e, i) => ({ ...e, seq: i + 1 }) as StreamEvent)
}

/** Canned scenarios, chosen by a keyword in the question (see handlers). */
export const scenarios: Record<string, StreamEvent[]> = {
  done: stream('done', 'Revenue was 4.2 million in fiscal 2025.', false, [citation]),
  abstained: stream('abstained', "I don't know based on the provided documents.", true, []),
  max_steps: stream('max_steps', 'Stopped: step limit reached.', false, []),
  max_tokens: stream('max_tokens', 'Stopped: token limit reached.', false, []),
  max_usd: stream('max_usd', 'Stopped: cost limit reached.', false, []),
  error: stream('error', '', false, []),
}
