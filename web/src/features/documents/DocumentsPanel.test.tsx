import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { DocumentOut, UploadResult } from '../../api/contract'
import { initialDocuments } from '../../mocks/fixtures'
import { server } from '../../mocks/server'
import { DocumentsPanel } from './DocumentsPanel'

const doc = (over: Partial<DocumentOut>): DocumentOut => ({
  ...initialDocuments()[0],
  ...over,
})
const list = (items: DocumentOut[]) =>
  http.get('*/v1/documents', () => HttpResponse.json({ items, total: items.length }))

function Harness({ onSelect }: { onSelect?: (ids: string[]) => void }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  )
  const [sel, setSel] = useState<string[]>([])
  const set = (ids: string[]) => {
    setSel(ids)
    onSelect?.(ids)
  }
  return (
    <QueryClientProvider client={client}>
      <DocumentsPanel
        selected={sel}
        pollMs={20}
        onToggle={(id) => set(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id])}
        onSelectAll={set}
        onClear={() => set([])}
      />
    </QueryClientProvider>
  )
}

const file = (name: string, size = 5) => new File([new Uint8Array(size).fill(65)], name)

describe('DocumentsPanel', () => {
  it('shows loading, then rows with meta and pill, plus the reset notice', async () => {
    render(<Harness />)
    expect(screen.getByText(/loading documents/i)).toBeInTheDocument()
    expect(await screen.findByText('annual-report.pdf')).toBeInTheDocument()
    expect(screen.getByText('PDF · 12 pages · 117 KB')).toBeInTheDocument()
    expect(screen.getByText('Ready')).toBeInTheDocument()
    expect(screen.getByText(/demo data resets in 24 hours/i)).toBeInTheDocument()
  })

  it('shows the empty state', async () => {
    server.use(list([]))
    render(<Harness />)
    expect(await screen.findByText(/no documents yet/i)).toBeInTheDocument()
  })

  it('only ready docs are tickable; All/None drive the selection', async () => {
    server.use(
      list([
        doc({ doc_id: 'a', filename: 'a.pdf' }),
        doc({ doc_id: 'b', filename: 'b.txt', status: 'processing' }),
      ]),
    )
    const seen = vi.fn()
    render(<Harness onSelect={seen} />)
    const a = await screen.findByRole('checkbox', { name: /a\.pdf/ })
    expect(screen.getByRole('checkbox', { name: /b\.txt/ })).toBeDisabled()
    expect(screen.getByText('Indexing')).toBeInTheDocument()
    await userEvent.click(a)
    expect(a).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'None' }))
    expect(a).not.toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'All' }))
    expect(seen).toHaveBeenLastCalledWith(['a'])
  })

  it('polls until a processing document becomes ready', async () => {
    let calls = 0
    server.use(
      http.get('*/v1/documents', () => {
        calls++
        const items = [doc({ status: calls < 3 ? 'processing' : 'ready' })]
        return HttpResponse.json({ items, total: 1 })
      }),
    )
    render(<Harness />)
    expect(await screen.findByText('Indexing')).toBeInTheDocument()
    expect(await screen.findByText('Ready')).toBeInTheDocument()
  })

  it('rejects the 11th file and pre-checks type and size', async () => {
    const posted = vi.fn()
    server.use(
      http.post('*/v1/documents', async ({ request }) => {
        posted((await request.formData()).getAll('files').length)
        return HttpResponse.json({ results: [] })
      }),
    )
    render(<Harness />)
    await screen.findByText('annual-report.pdf')
    const input = screen.getByLabelText('Upload documents')
    const many = Array.from({ length: 11 }, (_, i) => file(`f${i}.txt`))
    await userEvent.upload(input, many)
    expect(await screen.findByRole('alert')).toHaveTextContent(/at most 10/i)
    expect(
      within(screen.getByLabelText('Upload problems')).getByText('f10.txt'),
    ).toBeInTheDocument()
    await waitFor(() => expect(posted).toHaveBeenCalledWith(10))

    posted.mockClear()
    await userEvent
      .setup({ applyAccept: false })
      .upload(input, [file('x.exe'), file('big.pdf', 10 * 1024 * 1024 + 1)])
    const errs = await screen.findByLabelText('Upload problems')
    expect(within(errs).getByText('x.exe').parentElement).toHaveTextContent(
      /unsupported file type/i,
    )
    expect(within(errs).getByText('big.pdf').parentElement).toHaveTextContent(/too large/i)
    expect(posted).not.toHaveBeenCalled()
  })

  it('shows per-file server errors beside the right filename', async () => {
    const results: UploadResult[] = [
      { filename: 'good.txt', ok: true, doc_id: 'n1', status: 'ready', deduplicated: false },
      {
        filename: 'bad.pdf',
        ok: false,
        deduplicated: false,
        error: { code: 'content_mismatch', detail: 'x' },
      },
    ]
    server.use(http.post('*/v1/documents', () => HttpResponse.json({ results })))
    render(<Harness />)
    await screen.findByText('annual-report.pdf')
    await userEvent.upload(screen.getByLabelText('Upload documents'), [
      file('good.txt'),
      file('bad.pdf'),
    ])
    const errs = await screen.findByLabelText('Upload problems')
    expect(within(errs).getAllByRole('listitem')).toHaveLength(1)
    expect(within(errs).getByText('bad.pdf').parentElement).toHaveTextContent(/does not match/i)
  })

  it('shows per-file errors from an all-failed 415 response', async () => {
    const results: UploadResult[] = [
      {
        filename: 'a.pdf',
        ok: false,
        deduplicated: false,
        error: { code: 'content_mismatch', detail: 'x' },
      },
    ]
    server.use(http.post('*/v1/documents', () => HttpResponse.json({ results }, { status: 415 })))
    render(<Harness />)
    await screen.findByText('annual-report.pdf')
    await userEvent.upload(screen.getByLabelText('Upload documents'), [file('a.pdf')])
    expect(await screen.findByLabelText('Upload problems')).toHaveTextContent('a.pdf')
  })

  it('deletes after in-page confirmation and removes the row', async () => {
    render(<Harness />)
    await userEvent.click(await screen.findByRole('button', { name: 'Delete annual-report.pdf' }))
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm delete' }))
    await waitFor(() => expect(screen.queryByText('annual-report.pdf')).not.toBeInTheDocument())
  })

  it('cancel keeps the row; document_busy restores it with a message', async () => {
    server.use(
      http.delete('*/v1/documents/:id', () =>
        HttpResponse.json({ detail: 'busy', code: 'document_busy' }, { status: 409 }),
      ),
    )
    render(<Harness />)
    await userEvent.click(await screen.findByRole('button', { name: 'Delete annual-report.pdf' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Delete annual-report.pdf' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm delete' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/still being processed/i)
    expect(screen.getByText('annual-report.pdf')).toBeInTheDocument()
  })

  it('renders hostile filenames as text only', async () => {
    const name = '<img src=x onerror=alert(1)>.pdf'
    server.use(list([doc({ filename: name })]))
    const { container } = render(<Harness />)
    expect(await screen.findByText(name)).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('exposes labelled controls', async () => {
    render(<Harness />)
    await screen.findByText('annual-report.pdf')
    expect(screen.getByLabelText('Upload documents')).toHaveAttribute('type', 'file')
    expect(screen.getByRole('button', { name: 'Delete annual-report.pdf' })).toBeInTheDocument()
  })

  it('stops polling once nothing is indexing', async () => {
    let calls = 0
    server.use(
      http.get('*/v1/documents', () => {
        calls++
        return HttpResponse.json({ items: [doc({})], total: 1 })
      }),
    )
    render(<Harness />)
    await screen.findByText('Ready')
    await new Promise((r) => setTimeout(r, 150))
    const settled = calls
    await new Promise((r) => setTimeout(r, 150))
    expect(calls).toBe(settled)
    expect(settled).toBe(1)
  })

  it('shows an alert when listing fails', async () => {
    server.use(
      http.get('*/v1/documents', () =>
        HttpResponse.json({ detail: 'x', code: 'internal' }, { status: 500 }),
      ),
    )
    render(<Harness />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/something went wrong/i)
  })

  it('notes deduplicated uploads', async () => {
    const results: UploadResult[] = [
      { filename: 'same.txt', ok: true, doc_id: 'd1', status: 'ready', deduplicated: true },
    ]
    server.use(http.post('*/v1/documents', () => HttpResponse.json({ results })))
    render(<Harness />)
    await screen.findByText('annual-report.pdf')
    await userEvent.upload(screen.getByLabelText('Upload documents'), [file('same.txt')])
    expect(await screen.findByText(/already uploaded: same\.txt/i)).toBeInTheDocument()
  })
})
