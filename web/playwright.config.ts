import { defineConfig, devices } from '@playwright/test'

// E2E runs chromium only, against the mock build (VITE_MOCK=1, MSW in a service worker) served
// with the mock vercel.json headers so the CSP is enforced. No backend, localhost only.
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  fullyParallel: true,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:4173',
    // Playwright route() cannot see service-worker responses, so specs use the in-page seam.
    serviceWorkers: 'allow',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run build:e2e && node e2e/server.mjs',
    url: 'http://localhost:4173',
    reuseExistingServer: false,
    timeout: 180_000,
  },
})
