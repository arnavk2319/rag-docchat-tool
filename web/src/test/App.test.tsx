import { render, screen } from '@testing-library/react'
import { RouterProvider } from 'react-router-dom'
import { createTestRouter } from '../App'

describe('App routes', () => {
  it('renders /documents with the documents pane', () => {
    render(<RouterProvider router={createTestRouter('/documents')} />)
    expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument()
  })
  it('renders /chat/:sessionId', () => {
    render(<RouterProvider router={createTestRouter('/chat/s1')} />)
    expect(screen.getByRole('heading', { name: 'Chat' })).toBeInTheDocument()
  })
  it('redirects / to /documents', () => {
    render(<RouterProvider router={createTestRouter('/')} />)
    expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument()
  })
})
