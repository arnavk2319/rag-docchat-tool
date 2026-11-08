import { createBrowserRouter, createMemoryRouter, Navigate, Outlet } from 'react-router-dom'
import type { RouteObject } from 'react-router-dom'
import Chat from './pages/Chat'
import Documents from './pages/Documents'

export const routes: RouteObject[] = [
  {
    element: (
      <main className="mx-auto max-w-[1320px] p-4">
        <Outlet />
      </main>
    ),
    children: [
      { index: true, element: <Navigate to="/documents" replace /> },
      { path: '/documents', element: <Documents /> },
      { path: '/chat/:sessionId?', element: <Chat /> },
    ],
  },
]

export const createAppRouter = () => createBrowserRouter(routes)
export const createTestRouter = (initial: string) =>
  createMemoryRouter(routes, { initialEntries: [initial] })
