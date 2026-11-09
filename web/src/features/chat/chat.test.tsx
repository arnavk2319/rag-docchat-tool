import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { RouterProvider } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { createTestRouter } from '../../App'
import type { StreamEvent } from '../../api/contract'
import { citation, initialDocuments, MOCK_RUN_ID } from '../../mocks/fixtures'
import { sseResponse } from '../../mocks/sse'
import { server } from '../../mocks/server'
import { reducer, initialState } from './reducer'

function app(path = '/chat') {
  render(<RouterProvider router={createTestRouter(path)} />)
}

async function tick(name: RegExp = /annual-report\.pdf/) {
  await userEvent.click(await screen.findByRole('checkbox', { name }))
}

async function ask(q: string) {
  const box = screen.getByRole('textbox', { name: 'Your question' })
  await userEvent.type(box, q)
  await userEvent.click(screen.getByRole('button', { name: 'Send' }))
}

const base = { run_id: MOCK_RUN_ID }
const fin = (over: object = {}): StreamEvent =>
  ({
    ...base,
    seq: 99,
    event: 'final',
    answer: 'A',
    citations: [],
    abstained: false,
    stopped_reason: 'done',
    usage: { steps: 1, tokens: 2, usd: 0.5 },
    ...over,
  }) as StreamEvent

const custom = (evs: StreamEvent[]) => server.use(http.post('*/v1/query', () => sseResponse(evs)))

describe('scope and composer', () => {
  it('disables the composer with a hint until a document is ticked', async () => {
    app()
    const box = screen.getByRole('textbox', { name: 'Your question' })
    expect(box).toBeDisabled()
    expect(box).toHaveAttribute('placeholder', 'Select a document to start')
    expect(screen.getByText(/Tick at least one ready document/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    await tick()
    expect(box).toBeEnabled()
    expect(box).toHaveAttribute('placeholder', 'Ask about annual-report.pdf')
  })

  it('placeholder counts documents when several are ticked', async () => {
    server.use(
      http.get('*/v1/documents', () => {
        const a = initialDocuments()[0]
        return HttpResponse.json({
          items: [a, { ...a, doc_id: 'doc-2', filename: 'b.pdf' }],
          total: 2,
        })
      }),
    )
    app()
    await userEvent.click(await screen.findByRole('button', { name: 'All' }))
    expect(screen.getByRole('textbox', { name: 'Your question' })).toHaveAttribute(
      'placeholder',
      'Ask about 2 documents',
    )
  })

  it('sends doc_ids and shows a scope note when ticks change in a non-empty chat', async () => {
    let body: Record<string, unknown> = {}
    server.use(
      http.post('*/v1/query', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return sseResponse([fin()])
      }),
    )
    app('/chat/sess-1')
    await tick()
    await ask('hello')
    await screen.findByText('Completed', { exact: false })
    expect(body).toMatchObject({ doc_ids: ['doc-1'], session_id: 'sess-1', stream: true })
    await tick()
    expect(await screen.findByText('Scope changed: now 0 documents')).toBeInTheDocument()
  })

  it('blocks empty and oversize questions client-side', async () => {
    let posts = 0
    server.use(
      http.post('*/v1/query', () => {
        posts++
        return sseResponse([fin()])
      }),
    )
    app()
    await tick()
    const send = screen.getByRole('button', { name: 'Send' })
    expect(send).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox', { name: 'Your question' }), '   ')
    expect(send).toBeDisabled()
    await userEvent.click(screen.getByRole('textbox', { name: 'Your question' }))
    await userEvent.paste('x'.repeat(2001))
    expect(send).toBeDisabled()
    expect(screen.getByText(/too long/)).toBeInTheDocument()
    expect(posts).toBe(0)
  })
})

describe('panel to chat wiring', () => {
  it('ticking a document in the panel changes the doc_ids sent by the next query', async () => {
    const bodies: Array<{ doc_ids: string[] }> = []
    server.use(
      http.get('*/v1/documents', () => {
        const a = initialDocuments()[0]
        return HttpResponse.json({
          items: [a, { ...a, doc_id: 'doc-2', filename: 'b.pdf' }],
          total: 2,
        })
      }),
      http.post('*/v1/query', async ({ request }) => {
        bodies.push((await request.json()) as { doc_ids: string[] })
        return sseResponse([fin()])
      }),
    )
    app()
    await tick(/annual-report\.pdf/)
    await ask('one')
    await screen.findByText('Completed', { exact: false })
    await tick(/b\.pdf/)
    await ask('two')
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[0].doc_ids).toEqual(['doc-1'])
    expect(bodies[1].doc_ids).toEqual(['doc-1', 'doc-2'])
  })

  it('names a freshly listed document in the placeholder without a manual refresh', async () => {
    app()
    await tick()
    expect(screen.getByRole('textbox', { name: 'Your question' })).toHaveAttribute(
      'placeholder',
      'Ask about annual-report.pdf',
    )
  })
})

describe('scope pruning', () => {
  type Doc = ReturnType<typeof initialDocuments>[number]
  function twoDocs(statusB: Doc['status'] = 'ready') {
    const a = initialDocuments()[0]
    const state: { docs: Doc[]; bodies: Array<{ doc_ids: string[] }> } = {
      docs: [a, { ...a, doc_id: 'doc-2', filename: 'b.pdf', status: statusB }],
      bodies: [],
    }
    server.use(
      http.get('*/v1/documents', () =>
        HttpResponse.json({ items: state.docs, total: state.docs.length }),
      ),
      http.delete('*/v1/documents/:id', ({ params }) => {
        state.docs = state.docs.filter((d) => d.doc_id !== params.id)
        return new HttpResponse(null, { status: 204 })
      }),
      http.post('*/v1/query', async ({ request }) => {
        state.bodies.push((await request.json()) as { doc_ids: string[] })
        return sseResponse([fin()])
      }),
    )
    return state
  }

  it('deleting a ticked document removes it from the next query doc_ids', async () => {
    const st = twoDocs()
    app()
    await tick(/annual-report\.pdf/)
    await tick(/b\.pdf/)
    await userEvent.click(screen.getByRole('button', { name: 'Delete b.pdf' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm delete' }))
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: /b\.pdf/ })).toBeNull())
    await ask('after delete')
    await waitFor(() => expect(st.bodies).toHaveLength(1))
    expect(st.bodies[0].doc_ids).toEqual(['doc-1'])
  })

  it('drops unknown ids from a persisted per-session scope', async () => {
    const st = twoDocs()
    server.use(
      http.get('*/v1/sessions/sess-9', () =>
        HttpResponse.json({ session_id: 'sess-9', messages: [] }),
      ),
    )
    sessionStorage.setItem('docchat.scope', JSON.stringify({ 'sess-9': ['doc-1', 'ghost'] }))
    app('/chat/sess-9')
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: /annual-report/ })).toBeChecked(),
    )
    await ask('persisted')
    await waitFor(() => expect(st.bodies).toHaveLength(1))
    expect(st.bodies[0].doc_ids).toEqual(['doc-1'])
  })

  it('keeps the selection while the list call fails', async () => {
    server.use(
      http.get('*/v1/documents', () =>
        HttpResponse.json({ detail: 'x', code: 'internal' }, { status: 500 }),
      ),
    )
    server.use(
      http.get('*/v1/sessions/sess-9', () =>
        HttpResponse.json({ session_id: 'sess-9', messages: [] }),
      ),
    )
    sessionStorage.setItem('docchat.scope', JSON.stringify({ 'sess-9': ['doc-1'] }))
    app('/chat/sess-9')
    expect(await screen.findByRole('textbox', { name: 'Your question' })).toBeEnabled()
  })

  it('a document turning from processing to ready can be ticked', async () => {
    const st = twoDocs('processing')
    app()
    const cb = await screen.findByRole('checkbox', { name: /b\.pdf/ })
    expect(cb).toBeDisabled()
    st.docs = st.docs.map((d) => ({ ...d, status: 'ready' as const }))
    await waitFor(() => expect(cb).toBeEnabled(), { timeout: 5000 })
    await userEvent.click(cb)
    await ask('now ready')
    await waitFor(() => expect(st.bodies).toHaveLength(1))
    expect(st.bodies[0].doc_ids).toEqual(['doc-2'])
  })
})

describe('streaming', () => {
  it('shows ordered trace, answer, citation chip and the final stop line', async () => {
    app()
    await tick()
    await ask('revenue?')
    const chip = await screen.findByRole('button', { name: 'annual-report.pdf · p.1' })
    expect(chip).toBeInTheDocument()
    expect(screen.getByTestId('answer')).toHaveTextContent(
      'Revenue was 4.2 million in fiscal 2025.',
    )
    const items = within(screen.getByTestId('trace')).getAllByRole('listitem')
    expect(items.map((li) => li.getAttribute('data-kind'))).toEqual([
      'step',
      'tool_call',
      'tool_result',
      'step',
    ])
    expect(items[0]).toHaveTextContent('router · Routing question')
    expect(screen.getByTestId('stop-reason')).toHaveTextContent('Completed · 3 steps · 900 tokens')
    expect(screen.getByTestId('trace')).not.toHaveAttribute('open')
  })

  it.each([
    ['abstained', "I don't know"],
    ['max_steps', 'step limit reached'],
    ['max_tokens', 'token budget reached'],
    ['max_usd', 'cost budget reached'],
    ['error', 'an error occurred'],
  ])('renders a distinct message for %s', async (key, label) => {
    app()
    await tick()
    await ask(key)
    const el = await screen.findByTestId('stop-reason')
    expect(el).toHaveAttribute('data-stop', key)
    expect(el).toHaveTextContent(label)
  })

  it('shows the mapped text for an error event', async () => {
    app()
    await tick()
    await ask('error')
    expect(await screen.findByRole('alert')).toHaveTextContent('language model is unavailable')
  })

  it('keeps the trace open and Send disabled while streaming; Stop leaves a stopped state', async () => {
    server.use(
      http.post('*/v1/query', () => {
        const enc = new TextEncoder()
        const body = new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(
              enc.encode(
                `event: step\ndata: ${JSON.stringify({ ...base, seq: 1, node: 'plan', n: 1, summary: 'Planning' })}\n\n`,
              ),
            )
          },
        })
        return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }),
    )
    app()
    await tick()
    await ask('slow')
    await screen.findByText('plan · Planning')
    expect(screen.getByTestId('trace')).toHaveAttribute('open')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(await screen.findByText('Stopped by you')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Your question' })).toBeEnabled()
  })

  it('a stream cut before final shows an error and re-enables input', async () => {
    custom([{ ...base, seq: 1, event: 'token', text: 'partial' }])
    app()
    await tick()
    await ask('cut')
    expect(await screen.findByRole('alert')).toHaveTextContent('ended unexpectedly')
    expect(screen.getByRole('textbox', { name: 'Your question' })).toBeEnabled()
  })

  it.each([
    [429, 'rate_limited', 'Too many requests'],
    [429, 'spend_cap_exceeded', 'daily demo spend limit'],
    [429, 'budget_exceeded', 'exceeded its budget'],
    [503, 'llm_unavailable', 'language model is unavailable'],
  ])('maps pre-stream %i %s and re-enables input', async (status, code, text) => {
    server.use(
      http.post('*/v1/query', () => HttpResponse.json({ detail: 'raw', code }, { status })),
    )
    app()
    await tick()
    await ask('x')
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(screen.queryByText('raw')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Your question' })).toBeEnabled()
  })
})

describe('text-only rendering', () => {
  it('renders the answer as pre-wrap text; no HTML is parsed; entities decode to characters', async () => {
    const evil = '<img src=x onerror=alert(1)> & <b>bold</b>\nline2'
    const escaped = '&lt;img src=x onerror=alert(1)&gt; &amp; &lt;b&gt;bold&lt;/b&gt;\nline2'
    custom([{ ...base, seq: 1, event: 'token', text: escaped }, fin({ answer: escaped })])
    app()
    await tick()
    await ask('x')
    const answer = await screen.findByTestId('answer')
    expect(answer.textContent).toBe(evil)
    expect(answer.children).toHaveLength(0)
    expect(answer.className).toContain('whitespace-pre-wrap')
    expect(document.querySelector('img')).toBeNull()
  })

  it('decodes tokens as they stream (reducer)', () => {
    let s = reducer(initialState, { type: 'send', question: 'q' })
    s = reducer(s, { type: 'event', event: { ...base, seq: 1, event: 'token', text: '1 &lt; 2 ' } })
    s = reducer(s, { type: 'event', event: { ...base, seq: 2, event: 'token', text: '&amp; 3' } })
    const m = s.messages[1]
    expect(m.role === 'assistant' && m.answer).toBe('1 < 2 & 3')
  })
})

describe('source pane', () => {
  const passage = (over: object) =>
    server.use(
      http.get('*/v1/documents/:d/chunks/:c', () =>
        HttpResponse.json({
          ...{
            doc_id: 'doc-1',
            chunk_id: citation.chunk_id,
            filename: 'annual-report.pdf',
            page: 1,
            sheet: null,
            text: 'abcdef',
            highlight: { start: 2, end: 4 },
          },
          ...over,
        }),
      ),
    )

  async function openChip() {
    app()
    await tick()
    await ask('revenue?')
    await userEvent.click(await screen.findByRole('button', { name: 'annual-report.pdf · p.1' }))
  }

  it('shows loading then the passage with the highlight in <mark>', async () => {
    passage({})
    await openChip()
    const mark = await screen.findByText('cd', { selector: 'mark' })
    expect(mark.parentElement?.textContent).toBe('abcdef')
    expect(screen.getByText('page 1 · characters 0–42')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'annual-report.pdf · p.1' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('renders a <script> passage as text and creates no element', async () => {
    passage({ text: '<script>alert(1)</script>x', highlight: { start: 0, end: 8 } })
    await openChip()
    await screen.findByText('<script>', { selector: 'mark' })
    expect(document.querySelector('script')).toBeNull()
  })

  it.each([
    ['start >= end', { start: 3, end: 3 }],
    ['start > end', { start: 4, end: 2 }],
    ['end beyond text', { start: 1, end: 99 }],
    ['negative start', { start: -2, end: 3 }],
  ])('shows plain text without <mark> for invalid highlight (%s)', async (_n, highlight) => {
    passage({ text: 'abcdef', highlight })
    await openChip()
    const box = await screen.findByText('abcdef')
    expect(box.querySelector('mark')).toBeNull()
  })

  it('counts highlight offsets in code points (astral character before the highlight)', async () => {
    passage({ text: 'a😀bcd', highlight: { start: 2, end: 4 } })
    await openChip()
    const mark = await screen.findByText('bc', { selector: 'mark' })
    expect(mark.parentElement?.textContent).toBe('a😀bcd')
  })

  it('renders 404 and error states', async () => {
    server.use(
      http.get('*/v1/documents/:d/chunks/:c', () =>
        HttpResponse.json({ detail: 'x', code: 'not_found' }, { status: 404 }),
      ),
    )
    await openChip()
    expect(await screen.findByText(/no longer available/)).toBeInTheDocument()
  })

  it('renders a generic error state on 500', async () => {
    server.use(
      http.get('*/v1/documents/:d/chunks/:c', () =>
        HttpResponse.json({ detail: 'x', code: 'internal' }, { status: 500 }),
      ),
    )
    await openChip()
    await waitFor(() => expect(screen.getByText(/Something went wrong/)).toBeInTheDocument())
  })
})

describe('narrow layout', () => {
  it('shows one pane at a time with tabs at <=900px', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: true,
      media: q,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }))
    try {
      app('/chat')
      expect(screen.getByRole('tablist')).toBeInTheDocument()
      expect(screen.getByRole('log')).toBeInTheDocument()
      expect(screen.queryByLabelText('Source passage')).not.toBeInTheDocument()
      await userEvent.click(screen.getByRole('tab', { name: 'Source' }))
      expect(screen.getByLabelText('Source passage')).toBeInTheDocument()
      expect(screen.queryByRole('log')).not.toBeInTheDocument()
      await userEvent.click(screen.getByRole('tab', { name: 'Documents' }))
      expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('shows all three panes and no tabs on desktop', () => {
    app('/chat')
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Source passage')).toBeInTheDocument()
    expect(screen.getByRole('log')).toBeInTheDocument()
  })
})
