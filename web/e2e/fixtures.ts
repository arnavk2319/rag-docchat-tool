import { expect, test as base } from '@playwright/test'
import type { Page } from '@playwright/test'

type Handler = (info: { request: Request }) => Response | Promise<Response>
interface MockApi {
  worker: {
    use: (...h: unknown[]) => void
    events: { on: (n: string, cb: (i: { request: Request }) => void) => void }
  }
  http: Record<'get' | 'post', (path: string, h: Handler, o?: { once?: boolean }) => unknown>
}
declare global {
  interface Window {
    __mock?: MockApi
    __csp?: string[]
    __pwned?: unknown
    __queries?: unknown[]
  }
}

/** Expected console errors for a test (e.g. the browser logs every 4xx fetch). */
export interface Allow {
  console: RegExp[]
}

export const test = base.extend<{ allow: Allow }>({
  allow: [
    async ({ page }, provide) => {
      const problems: string[] = []
      const allow: Allow = { console: [] }
      page.on('console', (m) => {
        if (m.type() !== 'error') return
        const t = m.text()
        if (!allow.console.some((r) => r.test(t))) problems.push(`console.error: ${t}`)
      })
      page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
      await page.addInitScript(() => {
        window.__csp = []
        document.addEventListener('securitypolicyviolation', (e) =>
          window.__csp!.push(`${e.violatedDirective} ${e.blockedURI}`),
        )
      })
      await provide(allow)
      const csp = await page.evaluate(() => window.__csp ?? []).catch(() => [])
      for (const c of csp) problems.push(`securitypolicyviolation: ${c}`)
      expect(problems, 'console/page/CSP problems').toEqual([])
    },
    { auto: true },
  ],
})
export { expect }

export const sse = (events: object[]): string =>
  events
    .map((e, i) => {
      const { event, ...rest } = e as { event: string }
      return `event: ${event}\ndata: ${JSON.stringify({ ...rest, seq: i + 1 })}\n\n`
    })
    .join('')

/** Opens the app and waits until the mock worker is installed and documents are listed. */
export async function open(page: Page, path = '/chat'): Promise<void> {
  await page.goto(path)
  await page.waitForFunction(() => window.__mock !== undefined)
  await expect(page.getByRole('checkbox', { name: /annual-report/ })).toBeVisible()
}

/** Overrides POST /v1/query with timed raw SSE chunks (wait is ms before the chunk). */
export async function streamQuery(
  page: Page,
  chunks: { text: string; wait?: number }[],
  opts: { hold?: boolean } = {},
): Promise<void> {
  await page.evaluate(
    ({ chunks, hold }) => {
      const m = window.__mock!
      m.worker.use(
        m.http.post('*/v1/query', () => {
          const enc = new TextEncoder()
          let cancelled = false
          const body = new ReadableStream<Uint8Array>({
            async start(c) {
              for (const ch of chunks) {
                if (ch.wait) await new Promise((r) => setTimeout(r, ch.wait))
                if (cancelled) return
                c.enqueue(enc.encode(ch.text))
              }
              if (hold) await new Promise((r) => setTimeout(r, 30_000))
              if (!cancelled) c.close()
            },
            cancel() {
              cancelled = true
            },
          })
          return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
        }),
      )
    },
    { chunks, hold: opts.hold ?? false },
  )
}

/** Overrides one endpoint with a JSON error response (optionally only once). */
export async function failNext(
  page: Page,
  method: 'get' | 'post',
  path: string,
  status: number,
  code: string,
  once = false,
): Promise<void> {
  await page.evaluate(
    ({ method, path, status, code, once }) => {
      const m = window.__mock!
      m.worker.use(
        m.http[method](
          path,
          () =>
            new Response(JSON.stringify({ detail: code, code }), {
              status,
              headers: { 'Content-Type': 'application/json' },
            }),
          { once },
        ),
      )
    },
    { method, path, status, code, once },
  )
}

export async function ask(page: Page, q: string): Promise<void> {
  await page.getByRole('textbox', { name: 'Your question' }).fill(q)
  await page.getByRole('button', { name: 'Send' }).click()
}

/** Records JSON bodies of POST /v1/query seen by the mock worker (page.on('request') cannot read them). */
export async function recordQueries(page: Page): Promise<() => Promise<{ doc_ids: string[] }[]>> {
  await page.evaluate(() => {
    window.__queries = []
    window.__mock!.worker.events.on('request:start', ({ request }) => {
      if (request.method === 'POST' && request.url.endsWith('/v1/query'))
        void request
          .clone()
          .json()
          .then((b: unknown) => window.__queries!.push(b))
    })
  })
  return () => page.evaluate(() => window.__queries as { doc_ids: string[] }[])
}
