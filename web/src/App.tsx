import { createBrowserRouter, createMemoryRouter, Navigate } from 'react-router-dom'
import type { RouteObject } from 'react-router-dom'
import AppProviders from './AppProviders'
import NotFound from './NotFound'
import { docIntelEnabled, INTEL_KINDS } from './features/docintel/flag'

// Doc-intel routes exist only with VITE_FEATURE_DOC_INTEL=1; otherwise they fall to NotFound.
export const makeRoutes = (): RouteObject[] => [
  { index: true, element: <Navigate to="/documents" replace /> },
  {
    element: <AppProviders />,
    children: [
      { path: '/documents', element: null },
      { path: '/chat/:sessionId?', element: null },
      ...(docIntelEnabled() ? INTEL_KINDS.map((k) => ({ path: `/${k.kind}`, element: null })) : []),
    ],
  },
  { path: '*', element: <NotFound /> },
]

export const createAppRouter = () => createBrowserRouter(makeRoutes())
export const createTestRouter = (initial: string) =>
  createMemoryRouter(makeRoutes(), { initialEntries: [initial] })
