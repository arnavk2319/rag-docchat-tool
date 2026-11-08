import { render, screen } from '@testing-library/react'
import { RouterProvider } from 'react-router-dom'
import { createTestRouter } from '../App'

describe('App routes', () => {
  it('renders /documents', () => {
    render(<RouterProvider router={createTestRouter('/documents')} />)
    expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument()
  })
  it('renders /chat', () => {
    render(<RouterProvider router={createTestRouter('/chat')} />)
    expect(screen.getByRole('heading', { name: 'Chat' })).toBeInTheDocument()
  })
  it('redirects / to /documents', () => {
    render(<RouterProvider router={createTestRouter('/')} />)
    expect(screen.getByRole('heading', { name: 'Documents' })).toBeInTheDocument()
  })
})
