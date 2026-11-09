import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { RouterProvider } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { createTestRouter } from '../../App'
import { sessions } from '../../mocks/fixtures'
import { sseResponse } from '../../mocks/sse'
import { server } from '../../mocks/server'
import { scenarios } from '../../mocks/fixtures'

function app(path = '/chat') {
  const router = createTestRouter(path)
  render(<RouterProvider router={router} />)
  return router
}

const rows = (n: number) =>
  Array.from({ length: n }, (_, i) => ({
    session_id: `s-${i}`,
    title: `Title ${i}`,
    updated_at: '2026-11-08T09:30:00Z',
  }))

describe('session list', () => {
  it('renders titles, empty state and load more', async () => {
    server.use(
      http.get('*/v1/sessions', ({ request }) => {
        const u = new URL(request.url)
        const off = Number(u.searchParams.get('offset') ?? 0)
        const lim = Number(u.searchParams.get('limit') ?? 50)
        return HttpResponse.json({ items: rows(25).slice(off, off + lim), total: 25 })
      }),
    )
    app()
    expect(await screen.findByText('Title 0')).toBeInTheDocument()
    expect(screen.queryByText('Title 24')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await screen.findByText('Title 24')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('shows an empty state', async () => {
    server.use(http.get('*/v1/sessions', () => HttpResponse.json({ items: [], total: 0 })))
    app()
    expect(await screen.findByText('No conversations yet.')).toBeInTheDocument()
  })

  it('renders a script title as text only', async () => {
    const title = '<script>alert(1)</script>'
    server.use(
      http.get('*/v1/sessions', () =>
        HttpResponse.json({
          items: [{ session_id: 'x', title, updated_at: '2026-11-08T09:30:00Z' }],
          total: 1,
        }),
      ),
    )
    const { container } = render(<RouterProvider router={createTestRouter('/chat')} />)
    expect(await screen.findByText(title)).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
  })
})

describe('switching', () => {
  it('selecting a session loads its messages and marks it active', async () => {
    const router = app()
    await userEvent.click(
      await screen.findByRole('button', { name: new RegExp(sessions[0].title) }),
    )
    expect(await screen.findByText('Revenue was 4.2 million.')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/chat/sess-1')
  })

  it('unknown id shows not-found with a New chat action', async () => {
    const router = app('/chat/nope')
    expect(await screen.findByText('Conversation not found')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'New chat' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/chat'))
    expect(screen.queryByText('Conversation not found')).not.toBeInTheDocument()
  })

  it('restores per-session scope', async () => {
    sessionStorage.setItem('docchat.scope', JSON.stringify({ 'sess-1': ['doc-1'] }))
    app('/chat/sess-1')
    expect(await screen.findByRole('checkbox', { name: /annual-report\.pdf/ })).toBeChecked()
  })

  it('first send creates a session, adopts the route and refetches the list', async () => {
    let sent: unknown
    server.events.on('request:start', () => {})
    server.use(
      http.post('*/v1/query', async ({ request }) => {
        sent = await request.json()
        return sseResponse(scenarios.done)
      }),
    )
    const router = app('/chat')
    await userEvent.click(await screen.findByRole('checkbox', { name: /annual-report\.pdf/ }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Your question' }), 'hi')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/chat\/sess-new-/))
    expect((sent as { session_id: string }).session_id).toMatch(/^sess-new-/)
    expect(await screen.findByText('New conversation')).toBeInTheDocument()
    // the streamed answer survived the route change
    expect(await screen.findByText('Revenue was 4.2 million in fiscal 2025.')).toBeInTheDocument()
  })

  it('switching sessions mid-stream aborts the stream and drops late events', async () => {
    const abort = vi.spyOn(AbortController.prototype, 'abort')
    let ctrl: ReadableStreamDefaultController<Uint8Array> | undefined
    const enc = new TextEncoder()
    server.use(
      http.post('*/v1/query', () => {
        const stream = new ReadableStream<Uint8Array>({
          start(c) {
            ctrl = c
          },
        })
        return new HttpResponse(stream, { headers: { 'content-type': 'text/event-stream' } })
      }),
    )
    app('/chat/sess-1')
    await userEvent.click(await screen.findByRole('checkbox', { name: /annual-report\.pdf/ }))
    await screen.findByText('Revenue was 4.2 million.')
    await userEvent.type(screen.getByRole('textbox', { name: 'Your question' }), 'hi')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByRole('button', { name: 'Stop' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '+ New' }))
    await waitFor(() => expect(abort).toHaveBeenCalled())
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument(),
    )
    // The old stream keeps emitting; none of it may reach the new transcript.
    try {
      ctrl?.enqueue(
        enc.encode(
          'event: token\ndata: {"run_id":"r","seq":1,"text":"LATE-TOKEN"}\n\n' +
            'event: final\ndata: {"run_id":"r","seq":2,"answer":"LATE-FINAL","citations":[],"abstained":false,"stopped_reason":"done","usage":{"steps":1,"tokens":1,"usd":0}}\n\n',
        ),
      )
    } catch {
      // already cancelled: also fine
    }
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByText(/LATE-/)).not.toBeInTheDocument()
    expect(screen.queryByText('Revenue was 4.2 million.')).not.toBeInTheDocument()
    expect(screen.getByText('Ask your first question')).toBeInTheDocument()
  })
})

describe('failures and double send', () => {
  async function typeAndSend(q = 'hi') {
    await userEvent.click(await screen.findByRole('checkbox', { name: /annual-report\.pdf/ }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Your question' }), q)
  }

  it.each([
    [429, 'rate_limited', /Too many requests/],
    [500, 'internal', /Something went wrong/],
  ])(
    'session create %i shows a mapped error, restores the question, sends no query',
    async (status, code, msg) => {
      let queries = 0
      server.use(
        http.post('*/v1/sessions', () => HttpResponse.json({ detail: 'x', code }, { status })),
        http.post('*/v1/query', () => {
          queries++
          return sseResponse(scenarios.done)
        }),
      )
      app('/chat')
      await typeAndSend('keep me')
      await userEvent.click(screen.getByRole('button', { name: 'Send' }))
      expect(await screen.findByText(msg)).toBeInTheDocument()
      const box = screen.getByRole('textbox', { name: 'Your question' })
      expect(box).toBeEnabled()
      expect(box).toHaveValue('keep me')
      expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
      expect(queries).toBe(0)
    },
  )

  it('double send creates exactly one session and one query', async () => {
    let creates = 0
    let queries = 0
    server.use(
      http.post('*/v1/sessions', async () => {
        creates++
        await new Promise((r) => setTimeout(r, 30))
        return HttpResponse.json({ session_id: 'sess-dbl' }, { status: 201 })
      }),
      http.post('*/v1/query', () => {
        queries++
        return sseResponse(scenarios.done)
      }),
    )
    app('/chat')
    await typeAndSend()
    const box = screen.getByRole('textbox', { name: 'Your question' })
    await userEvent.type(box, '{Enter}{Enter}')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('Revenue was 4.2 million in fiscal 2025.')).toBeInTheDocument()
    expect(creates).toBe(1)
    expect(queries).toBe(1)
  })
})
