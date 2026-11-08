import { createBrowserRouter, createMemoryRouter, Navigate } from 'react-router-dom'
import type { RouteObject } from 'react-router-dom'
import AppProviders from './AppProviders'

// Both routes render the same three-pane shell; /documents just opens on the Documents tab.
export const routes: RouteObject[] = [
  { index: true, element: <Navigate to="/documents" replace /> },
  {
    element: <AppProviders />,
    children: [
      { path: '/documents', element: null },
      { path: '/chat/:sessionId?', element: null },
    ],
  },
]

export const createAppRouter = () => createBrowserRouter(routes)
export const createTestRouter = (initial: string) =>
  createMemoryRouter(routes, { initialEntries: [initial] })
