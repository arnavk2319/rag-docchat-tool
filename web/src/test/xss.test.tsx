import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { RouterProvider } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { createTestRouter } from '../App'
import type { StreamEvent } from '../api/contract'
import { initialDocuments, MOCK_RUN_ID, sessions } from '../mocks/fixtures'
import { sseResponse } from '../mocks/sse'
import { server } from '../mocks/server'

// [raw payload sent by the server, text the user must literally see after the documented
// one-pass decode of the backend's `< > &` escaping]
const PAYLOADS: [string, string, string][] = [
  ['img onerror', '<img src=x onerror=alert(1)>', '<img src=x onerror=alert(1)>'],
  ['script tag', '<script>alert(1)</script>', '<script>alert(1)</script>'],
  ['javascript url', 'javascript:alert(document.cookie)', 'javascript:alert(document.cookie)'],
  ['https url from model', 'https://evil.example/x?a=1', 'https://evil.example/x?a=1'],
  ['entity-encoded img', '&lt;img src=x onerror=alert(1)&gt;', '<img src=x onerror=alert(1)>'],
  ['double-encoded', '&amp;lt;script&amp;gt;', '&lt;script&gt;'],
  ['numeric entity', '&#60;img src=x&#62;', '&#60;img src=x&#62;'],
  ['svg onload', '<svg onload=alert(1)>', '<svg onload=alert(1)>'],
]

const base = { run_id: MOCK_RUN_ID }

function streamWith(p: string): StreamEvent[] {
  const cit = {
    doc_id: 'doc-1',
    filename: `${p}.pdf`,
    chunk_id: 'doc-1:1:0',
    page: 1,
    sheet: p,
    span: { char_start: 0, char_end: 4 },
  }
  return [
    { ...base, seq: 1, event: 'step', node: 'plan', n: 1, summary: p },
    { ...base, seq: 2, event: 'tool_call', tool: p, args: { q: p, url: p } },
    { ...base, seq: 3, event: 'tool_result', tool: p, ok: true, n_items: 1 },
    { ...base, seq: 4, event: 'error', code: p, detail: p },
    {
      ...base,
      seq: 5,
      event: 'final',
      answer: p,
      citations: [cit],
      abstained: false,
      stopped_reason: 'done',
      usage: { steps: 1, tokens: 2, usd: 0.01 },
    },
  ]
}

function assertInert(container: HTMLElement) {
  expect(container.querySelector('img,script,a,svg,iframe,object,embed')).toBeNull()
  expect(container.querySelector('[onerror],[onload],[onclick]')).toBeNull()
}

describe.each(PAYLOADS)('XSS matrix: %s', (_name, raw, shown) => {
  it('renders chat answer, steps, tool args, citations, errors and filenames as text', async () => {
    const docs = initialDocuments()
    docs[0].filename = `${raw}.pdf`
    server.use(
      http.get('*/v1/documents', () => HttpResponse.json({ items: docs, total: 1 })),
      http.post('*/v1/query', () => sseResponse(streamWith(raw))),
    )
    const { container } = render(<RouterProvider router={createTestRouter('/chat')} />)
    await userEvent.click(await screen.findByRole('checkbox'))
    // filename in documents list is text
    expect(container.textContent).toContain(`${raw}.pdf`)
    await userEvent.type(screen.getByRole('textbox', { name: 'Your question' }), 'q')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    const answer = await screen.findByTestId('answer')

    expect(answer.textContent).toBe(shown)
    expect(answer.children).toHaveLength(0)
    const trace = screen.getByTestId('trace')
    expect(trace.textContent).toContain(`plan · ${shown}`)
    expect(trace.textContent).toContain(`call ${shown} q=${shown} url=${shown}`)
    for (const li of trace.querySelectorAll('li')) expect(li.children).toHaveLength(0)
    // citation chip label is the (decoded) filename text only
    const chip = screen.getByRole('button', { name: new RegExp('p\\.1$') })
    expect(chip.textContent).toBe(`${shown}.pdf · p.1`)
    expect(chip.children).toHaveLength(0)
    // error: mapped message only, raw code/detail never shown as markup
    const alert = screen.getByRole('alert')
    expect(alert.children).toHaveLength(0)
    expect(alert.textContent).not.toContain('<')

    assertInert(container)
    assertInert(document.body)
  })
})

describe('session titles', () => {
  it.each(PAYLOADS)('is inert when the title is %s', async (_n, raw, shown) => {
    // Works before and after T-042: if a sessions list renders titles they must be text.
    server.use(
      http.get('*/v1/sessions', () =>
        HttpResponse.json({
          items: [{ ...sessions[0], title: raw }],
          total: 1,
        }),
      ),
    )
    const { container } = render(<RouterProvider router={createTestRouter('/chat')} />)
    await screen.findByRole('checkbox')
    assertInert(container)
    const title = await screen.findByText(
      (_c, el) => el?.tagName === 'SPAN' && (el.textContent === raw || el.textContent === shown),
    )
    expect(title.children).toHaveLength(0)
  })
})

describe('model URLs', () => {
  it('are plain text, never anchors', async () => {
    server.use(
      http.post('*/v1/query', () =>
        sseResponse([
          {
            ...base,
            seq: 1,
            event: 'final',
            answer:
              'see https://evil.example/a and javascript:alert(1) and [x](https://evil.example)',
            citations: [],
            abstained: false,
            stopped_reason: 'done',
            usage: { steps: 1, tokens: 2, usd: 0.01 },
          },
        ]),
      ),
    )
    const { container } = render(<RouterProvider router={createTestRouter('/chat')} />)
    await userEvent.click(await screen.findByRole('checkbox'))
    await userEvent.type(screen.getByRole('textbox', { name: 'Your question' }), 'q')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    const answer = await screen.findByTestId('answer')
    expect(answer.textContent).toContain('https://evil.example/a')
    expect(container.querySelector('a, [href], [src]')).toBeNull()
  })
})

// The backend (sanitize_output) escapes & < > exactly once; the UI decodes once, renders text only.
const ESCAPED: [string, string, string][] = [
  ['img onerror', '&lt;img src=x onerror=alert(1)&gt;', '<img src=x onerror=alert(1)>'],
  ['script tag', '&lt;script&gt;alert(1)&lt;/script&gt;', '<script>alert(1)</script>'],
  ['svg onload', '&lt;svg onload=alert(1)&gt;', '<svg onload=alert(1)>'],
  ['ampersand', 'R&amp;D', 'R&D'],
  ['double-encoded', '&amp;lt;script&amp;gt;', '&lt;script&gt;'],
]

describe('doc-intel results are text only', () => {
  it.each(ESCAPED)('summary, sentiment and compare render %s as text', async (_n, sent, shown) => {
    vi.stubEnv('VITE_FEATURE_DOC_INTEL', '1')
    const cit = {
      doc_id: 'doc-1',
      filename: 'annual-report.pdf',
      chunk_id: 'doc-1:1:0',
      page: 1,
      sheet: null,
      span: { char_start: 0, char_end: 4 },
    }
    server.use(
      http.get('*/v1/documents', () => {
        const [a] = initialDocuments()
        return HttpResponse.json({
          items: [a, { ...a, doc_id: 'doc-2', filename: 'b.pdf' }],
          total: 2,
        })
      }),
      http.post('*/v1/documents/:id/summary', () =>
        HttpResponse.json({ doc_id: 'doc-1', summary: sent, citations: [cit] }),
      ),
      http.post('*/v1/documents/:id/sentiment', () =>
        HttpResponse.json({
          doc_id: 'doc-1',
          label: 'neutral',
          confidence: 0.5,
          rationale: sent,
          citations: [cit],
        }),
      ),
      http.post('*/v1/compare', () =>
        HttpResponse.json({
          doc_ids: ['doc-1', 'doc-2'],
          similarities: [{ point: sent, citations: [cit] }],
          differences: [{ point: sent, citations: [] }],
        }),
      ),
    )
    for (const [path, btn] of [
      ['/summary', 'Run summary'],
      ['/sentiment', 'Run sentiment'],
      ['/compare', 'Run compare'],
    ]) {
      const { container, unmount } = render(<RouterProvider router={createTestRouter(path)} />)
      await userEvent.click(await screen.findByRole('checkbox', { name: /annual-report/ }))
      await userEvent.click(screen.getByRole('checkbox', { name: /b\.pdf/ }))
      await userEvent.click(screen.getByRole('button', { name: btn }))
      const hits = await screen.findAllByText(shown)
      for (const h of hits) expect(h.textContent).toBe(shown)
      const view = container.querySelector('[data-testid^="intel-"]')!
      expect(
        view.querySelector('script, img, svg, iframe, a[href], [onerror], [onload]'),
      ).toBeNull()
      unmount()
    }
    vi.unstubAllEnvs()
  })
})
