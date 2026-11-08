import { http, HttpResponse } from 'msw'
import { setupWorker } from 'msw/browser'
import { handlers } from './handlers'

export const worker = setupWorker(...handlers)

// Test seam for Playwright (T-044): lets E2E specs add per-test handler overrides (429, one-shot
// 401, slow or hostile streams) via page.evaluate. Mock builds only: main.tsx imports this module
// solely when VITE_MOCK === '1', so the real bundle never contains it.
;(window as unknown as { __mock: unknown }).__mock = { worker, http, HttpResponse }
