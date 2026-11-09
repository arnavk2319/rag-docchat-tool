import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { createAppRouter } from './App'
import './styles.css'

async function enableMocks(): Promise<void> {
  // Statically replaced at build: with VITE_MOCK unset this branch (and msw) is dropped.
  if (import.meta.env.VITE_MOCK === '1') {
    const { worker } = await import('./mocks/browser')
    try {
      await worker.start()
    } catch {
      // mocks failed to start: render the app anyway (requests will hit the network)
    }
  }
}

void enableMocks().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <RouterProvider router={createAppRouter()} />
    </StrictMode>,
  )
})
