import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { RouterProvider } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createTestRouter } from '../../App'
import { citation as cit, initialDocuments, sessionDetail } from '../../mocks/fixtures'
import { server } from '../../mocks/server'

afterEach(() => vi.unstubAllEnvs())

function app(path: string, flag = '1') {
  vi.stubEnv('VITE_FEATURE_DOC_INTEL', flag)
  render(<RouterProvider router={createTestRouter(path)} />)
}

const tick = async (name = /annual-report/) =>
  userEvent.click(await screen.findByRole('checkbox', { name }))

const twoDocs = () => {
  const [a] = initialDocuments()
  return [a, { ...a, doc_id: 'doc-2', filename: 'second.pdf' }]
}
const listTwo = () =>
  server.use(
    http.get('*/v1/documents', () => HttpResponse.json({ items: twoDocs(), total: 2 })),
    http.post('*/v1/compare', () =>
      HttpResponse.json({
        doc_ids: ['doc-1', 'doc-2'],
        similarities: [{ point: 'Both documents report revenue.', citations: [cit] }],
        differences: [{ point: 'annual-report.pdf covers 2025 only.', citations: [cit] }],
      }),
    ),
  )

const err = (status: number, code: string) => HttpResponse.json({ detail: code, code }, { status })

describe('feature flag', () => {
  it('flag off: no nav and doc-intel routes are not found', async () => {
    app('/summary', '')
    expect(await screen.findByText('Page not found')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Views' })).not.toBeInTheDocument()
  })

  it('flag off: chat has no doc-intel links', async () => {
    app('/chat', '')
    await screen.findByRole('checkbox', { name: /annual-report/ })
    expect(screen.queryByRole('link', { name: 'Summary' })).not.toBeInTheDocument()
  })

  it('flag on: nav links show and routes render', async () => {
    app('/sentiment')
    const nav = await screen.findByRole('navigation', { name: 'Views' })
    expect(
      within(nav)
        .getAllByRole('link')
        .map((l) => l.textContent),
    ).toEqual(['Chat', 'Summary', 'Sentiment', 'Compare'])
    expect(screen.getByRole('heading', { name: 'Sentiment' })).toBeInTheDocument()
  })
})

describe('summary', () => {
  it('shows the budget hint, needs an explicit click, then renders summary with citations', async () => {
    let calls = 0
    server.events.on('request:start', ({ request }) => {
      if (request.url.endsWith('/summary')) calls++
    })
    app('/summary')
    expect(await screen.findByText(/Uses your daily budget/)).toBeInTheDocument()
    const run = screen.getByRole('button', { name: 'Run summary' })
    expect(run).toBeDisabled()
    await tick()
    expect(calls).toBe(0)
    await userEvent.click(run)
    expect(await screen.findByText(/Summary of annual-report.pdf/)).toBeInTheDocument()
    expect(calls).toBe(1)
    await userEvent.click(screen.getByRole('button', { name: /annual-report.pdf · p.1/ }))
    const pane = screen.getByRole('complementary', { name: 'Source passage' })
    expect(await within(pane).findByText(/Revenue was 4.2 million/)).toBeInTheDocument()
  })

  it('disables the button while running (loading state)', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>((r) => (release = r))
    server.use(
      http.post('*/v1/documents/:id/summary', async () => {
        await gate
        return HttpResponse.json({ doc_id: 'doc-1', summary: 'done text', citations: [] })
      }),
    )
    app('/summary')
    await tick()
    await userEvent.click(screen.getByRole('button', { name: 'Run summary' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Working')
    expect(screen.getByRole('button', { name: 'Running…' })).toBeDisabled()
    release()
    expect(await screen.findByText('done text')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run summary' })).toBeEnabled()
  })

  it('empty state with no ready documents', async () => {
    server.use(http.get('*/v1/documents', () => HttpResponse.json({ items: [], total: 0 })))
    app('/summary')
    expect(await screen.findByText(/No ready documents yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run summary' })).not.toBeInTheDocument()
  })

  it.each([
    ['rate_limited', 429, 'Too many requests'],
    ['spend_cap_exceeded', 429, 'daily demo spend limit'],
    ['budget_exceeded', 429, 'exceeded its budget'],
    ['document_busy', 409, 'still being processed'],
    ['not_ready', 409, 'not ready yet'],
    ['llm_unavailable', 502, 'unavailable'],
  ])('maps %s', async (code, status, text) => {
    server.use(http.post('*/v1/documents/:id/summary', () => err(status, code)))
    app('/summary')
    await tick()
    await userEvent.click(screen.getByRole('button', { name: 'Run summary' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(screen.getByRole('button', { name: 'Run summary' })).toBeEnabled()
  })

  it('stops after a spend cap error and skips the remaining documents', async () => {
    listTwo()
    let calls = 0
    server.use(
      http.post('*/v1/documents/:id/summary', () => {
        calls++
        return err(429, 'spend_cap_exceeded')
      }),
    )
    app('/summary')
    await tick()
    await tick(/second/)
    await userEvent.click(screen.getByRole('button', { name: 'Run summary' }))
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(2))
    expect(calls).toBe(1)
    expect(screen.getByText(/^Not run\./)).toBeInTheDocument()
  })

  it('shows partial and abstained flags', async () => {
    server.use(
      http.post('*/v1/documents/:id/summary', () =>
        HttpResponse.json({
          doc_id: 'doc-1',
          summary: '',
          citations: [],
          partial: true,
          abstained: true,
        }),
      ),
    )
    app('/summary')
    await tick()
    await userEvent.click(screen.getByRole('button', { name: 'Run summary' }))
    expect(await screen.findByText('Partial result')).toBeInTheDocument()
    expect(screen.getByText(/could not give a confident answer/)).toBeInTheDocument()
  })
})

describe('sentiment', () => {
  it('renders label, confidence, rationale and citations per document', async () => {
    app('/sentiment')
    await tick()
    await userEvent.click(screen.getByRole('button', { name: 'Run sentiment' }))
    expect(await screen.findByText('positive')).toBeInTheDocument()
    expect(screen.getByText('Confidence 82%')).toBeInTheDocument()
    expect(screen.getByText(/optimistic about growth/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /annual-report.pdf · p.1/ })).toBeInTheDocument()
  })

  it('maps a 404 on a vanished document', async () => {
    server.use(http.post('*/v1/documents/:id/sentiment', () => err(404, 'not_found')))
    app('/sentiment')
    await tick()
    await userEvent.click(screen.getByRole('button', { name: 'Run sentiment' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Not found.')
  })
})

describe('compare', () => {
  it('needs 2+ documents, then renders similarities and differences with citations', async () => {
    listTwo()
    app('/compare')
    const run = await screen.findByRole('button', { name: 'Run compare' })
    await tick()
    expect(run).toBeDisabled()
    expect(screen.getByText('Tick at least 2 documents.')).toBeInTheDocument()
    await tick(/second/)
    expect(run).toBeEnabled()
    await userEvent.type(screen.getByLabelText(/Focus/), 'revenue')
    await userEvent.click(run)
    expect(await screen.findByText('Both documents report revenue.')).toBeInTheDocument()
    expect(screen.getByText(/covers 2025 only/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /pdf · p.1/ }).length).toBeGreaterThan(1)
  })

  it('maps rate_limited', async () => {
    listTwo()
    server.use(http.post('*/v1/compare', () => err(429, 'rate_limited')))
    app('/compare')
    await tick()
    await tick(/second/)
    await userEvent.click(screen.getByRole('button', { name: 'Run compare' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many requests')
  })
})

describe('round 2', () => {
  it('budget_exceeded stops the loop and marks the rest Not run', async () => {
    listTwo()
    let calls = 0
    server.use(
      http.post('*/v1/documents/:id/summary', () => {
        calls++
        return err(429, 'budget_exceeded')
      }),
    )
    app('/summary')
    await tick()
    await tick(/second/)
    await userEvent.click(screen.getByRole('button', { name: 'Run summary' }))
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(2))
    expect(calls).toBe(1)
    expect(screen.getByText(/^Not run\./)).toBeInTheDocument()
  })

  it('document_busy and not_ready (409) show mapped messages on sentiment', async () => {
    for (const [code, text] of [
      ['document_busy', 'still being processed'],
      ['not_ready', 'not ready yet'],
    ]) {
      vi.stubEnv('VITE_FEATURE_DOC_INTEL', '1')
      server.use(http.post('*/v1/documents/:id/sentiment', () => err(409, code)))
      const { unmount } = render(<RouterProvider router={createTestRouter('/sentiment')} />)
      await tick()
      await userEvent.click(screen.getByRole('button', { name: 'Run sentiment' }))
      expect(await screen.findByRole('alert')).toHaveTextContent(text)
      unmount()
    }
  })

  it('double click fires exactly one request', async () => {
    let calls = 0
    server.use(
      http.post('*/v1/documents/:id/summary', async () => {
        calls++
        await new Promise((r) => setTimeout(r, 50))
        return HttpResponse.json({ doc_id: 'doc-1', summary: 'ok', citations: [] })
      }),
    )
    app('/summary')
    await tick()
    const run = screen.getByRole('button', { name: 'Run summary' })
    await userEvent.dblClick(run)
    await screen.findByText('ok')
    expect(calls).toBe(1)
  })

  it('compare with more than 5 ticked documents is disabled with a hint', async () => {
    const [a] = initialDocuments()
    const items = Array.from({ length: 6 }, (_, i) => ({
      ...a,
      doc_id: `d${i}`,
      filename: `file${i}.pdf`,
    }))
    server.use(http.get('*/v1/documents', () => HttpResponse.json({ items, total: 6 })))
    app('/compare')
    for (let i = 0; i < 6; i++) await tick(new RegExp(`file${i}`))
    expect(screen.getByRole('button', { name: 'Run compare' })).toBeDisabled()
    expect(screen.getByText('Tick at most 5 documents.')).toBeInTheDocument()
  })

  it('focus input is capped at 200 characters and sent', async () => {
    listTwo()
    let body: { focus?: string } = {}
    server.use(
      http.post('*/v1/compare', async ({ request }) => {
        body = (await request.json()) as { focus?: string }
        return HttpResponse.json({ doc_ids: ['doc-1', 'doc-2'], similarities: [], differences: [] })
      }),
    )
    app('/compare')
    await tick()
    await tick(/second/)
    const input = screen.getByLabelText(/Focus/)
    expect(input).toHaveAttribute('maxlength', '200')
    await userEvent.click(input)
    await userEvent.paste('x'.repeat(200))
    await userEvent.click(screen.getByRole('button', { name: 'Run compare' }))
    await waitFor(() => expect(body.focus).toHaveLength(200))
  })

  it('leaving the view stops the per-document loop', async () => {
    listTwo()
    let calls = 0
    server.use(
      http.post('*/v1/documents/:id/summary', async () => {
        calls++
        await new Promise((r) => setTimeout(r, 80))
        return HttpResponse.json({ doc_id: 'doc-1', summary: 'ok', citations: [] })
      }),
    )
    app('/summary')
    await tick()
    await tick(/second/)
    await userEvent.click(screen.getByRole('button', { name: 'Run summary' }))
    await waitFor(() => expect(calls).toBe(1))
    await userEvent.click(screen.getByRole('link', { name: 'Sentiment' }))
    await new Promise((r) => setTimeout(r, 250))
    expect(calls).toBe(1)
  })

  it('Chat stays mounted: draft and session survive Chat -> Summary -> Chat', async () => {
    app(`/chat/${sessionDetail.session_id}`)
    await tick()
    const box = await screen.findByRole('textbox', { name: 'Your question' })
    await userEvent.type(box, 'my draft')
    await userEvent.click(screen.getByRole('link', { name: 'Summary' }))
    expect(await screen.findByRole('heading', { name: 'Summary' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Chat' })).toHaveAttribute(
      'href',
      `/chat/${sessionDetail.session_id}`,
    )
    await userEvent.click(screen.getByRole('link', { name: 'Chat' }))
    expect(await screen.findByRole('textbox', { name: 'Your question' })).toHaveValue('my draft')
  })
})
