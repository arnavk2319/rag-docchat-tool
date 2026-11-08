import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { RouterProvider } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
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
    // TODO(T-042): once the sessions list exists, assert the title text node is found:
    //   expect(await screen.findByText(raw)).toBeInTheDocument()
    const hit = screen.queryAllByText(
      (_c, el) => el?.textContent === raw || el?.textContent === shown,
    )
    for (const el of hit) expect(el.children).toHaveLength(0)
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
