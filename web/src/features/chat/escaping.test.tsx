import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { RouterProvider } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { createTestRouter } from '../../App'
import { server } from '../../mocks/server'
import { initialState, reducer } from './reducer'

// Escaping matrix (see docs/deploy-web-smoke.md): history content and session titles are
// entity-escaped by the backend and must be decoded; raw fields (document filenames) must not.
describe('entity escaping of backend fields', () => {
  it('session history (both roles) is decoded on load', () => {
    const s = reducer(initialState, {
      type: 'load',
      messages: [
        { role: 'user', content: 'R&amp;D &lt;b&gt; costs?' },
        { role: 'assistant', content: 'R&amp;D was 1 &amp;lt; 2' },
      ],
    })
    const [u, a] = s.messages
    expect(u).toMatchObject({ text: 'R&D <b> costs?' })
    expect(a).toMatchObject({ answer: 'R&D was 1 &lt; 2' }) // single pass, no double decoding
  })

  it('session titles are decoded; raw document filenames are shown as-is', async () => {
    server.use(
      http.get('*/v1/sessions', () =>
        HttpResponse.json({
          items: [
            { session_id: 's1', title: 'R&amp;D &lt;plan&gt;', updated_at: '2026-11-08T09:30:00Z' },
          ],
          total: 1,
        }),
      ),
      http.get('*/v1/documents', () =>
        HttpResponse.json({
          items: [
            {
              doc_id: 'd1',
              filename: 'R&amp;D.pdf',
              mime: 'application/pdf',
              size: 10,
              sha256: 'a'.repeat(64),
              pages_or_sheets: 1,
              status: 'ready',
              failure_reason: null,
              created_at: '2026-11-08T09:00:00Z',
            },
          ],
          total: 1,
        }),
      ),
    )
    render(<RouterProvider router={createTestRouter('/chat')} />)
    expect(await screen.findByText('R&D <plan>')).toBeInTheDocument()
    expect(await screen.findByText('R&amp;D.pdf')).toBeInTheDocument() // raw field: not decoded
  })
})
