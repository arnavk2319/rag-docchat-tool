import { expect, test, ask, recordQueries, open, streamQuery, sse, failNext } from './fixtures'

const RUN = { run_id: 'r1' }
const tick = (page: import('@playwright/test').Page) =>
  page.getByRole('checkbox', { name: /annual-report/ })

test.describe('documents', () => {
  test('upload two files, one fails: per-file error, good one listed', async ({ page }) => {
    await open(page)
    await page.getByLabel('Upload documents').setInputFiles([
      { name: 'good.txt', mimeType: 'text/plain', buffer: Buffer.from('hello world') },
      { name: 'malware.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('x') },
    ])
    const problems = page.getByRole('list', { name: 'Upload problems' })
    await expect(problems).toContainText('malware.exe')
    await expect(problems).toContainText('Unsupported file type')
    await expect(page.getByRole('checkbox', { name: /good\.txt/ })).toBeVisible()
  })

  test('empty file is reported', async ({ page }) => {
    await open(page)
    await page
      .getByLabel('Upload documents')
      .setInputFiles([{ name: 'empty.txt', mimeType: 'text/plain', buffer: Buffer.alloc(0) }])
    await expect(page.getByRole('list', { name: 'Upload problems' })).toContainText(
      'The file is empty.',
    )
  })

  test('server-side upload failure shows a mapped message', async ({ page, allow }) => {
    allow.console.push(/429|Failed to load resource/)
    await open(page)
    await failNext(page, 'post', '*/v1/documents', 429, 'rate_limited')
    await page
      .getByLabel('Upload documents')
      .setInputFiles([{ name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('a') }])
    await expect(page.getByRole('alert').first()).toContainText('Too many requests')
  })

  test('delete a document with confirmation', async ({ page }) => {
    await open(page)
    await page.getByRole('button', { name: 'Delete annual-report.pdf' }).click()
    await page.getByRole('button', { name: 'Confirm delete' }).click()
    await expect(tick(page)).toHaveCount(0)
    await expect(page.getByText('No documents yet')).toBeVisible()
  })
})

test.describe('chat', () => {
  test('composer disabled until a document is ticked', async ({ page }) => {
    await open(page)
    await expect(page.getByRole('textbox', { name: 'Your question' })).toBeDisabled()
    await tick(page).check()
    await expect(page.getByRole('textbox', { name: 'Your question' })).toBeEnabled()
  })

  test('question: step timeline, streamed answer, citation chip opens source with <mark>', async ({
    page,
  }) => {
    await open(page)
    await tick(page).check()
    await ask(page, 'What was revenue?')
    const answer = page.getByTestId('answer')
    await expect(answer).toHaveText('Revenue was 4.2 million in fiscal 2025.')
    await expect(page.getByTestId('trace')).toContainText('Routing question')
    await expect(page.getByTestId('stop-reason')).toHaveAttribute('data-stop', 'done')
    await page.getByRole('button', { name: /annual-report\.pdf · p\.1/ }).click()
    const pane = page.getByRole('complementary', { name: 'Source passage' })
    await expect(pane).toContainText('annual-report.pdf')
    await expect(pane).toContainText('page 1')
    const mark = pane.locator('mark')
    await expect(mark).toHaveCount(1)
    await expect(mark).toContainText('Revenue was 4.2 million')
  })

  test('ticking and unticking changes doc_ids of the next query', async ({ page }) => {
    await open(page)
    await page
      .getByLabel('Upload documents')
      .setInputFiles([{ name: 'second.txt', mimeType: 'text/plain', buffer: Buffer.from('two') }])
    const second = page.getByRole('checkbox', { name: /second\.txt/ })
    await expect(second).toBeVisible()
    const queries = await recordQueries(page)
    await tick(page).check()
    await second.check()
    await ask(page, 'first')
    await expect(page.getByTestId('stop-reason')).toBeVisible()
    await tick(page).uncheck()
    await expect(page.getByText('Scope changed: now 1 document')).toBeVisible()
    await ask(page, 'second question')
    await expect(page.getByTestId('assistant')).toHaveCount(2)
    await expect.poll(async () => (await queries()).length).toBe(2)
    const bodies = await queries()
    expect(bodies[0].doc_ids).toHaveLength(2)
    expect(bodies[1].doc_ids).toHaveLength(1)
    expect(bodies[1].doc_ids[0]).toMatch(/^doc-new-/)
  })

  const reasons: [string, string, RegExp][] = [
    ['abstained', 'abstained', /I don't know/],
    ['max_steps', 'max_steps', /step limit/],
    ['max_tokens', 'max_tokens', /token budget/],
    ['max_usd', 'max_usd', /cost budget/],
    ['error', 'error', /an error occurred/],
  ]
  for (const [kw, reason, label] of reasons) {
    test(`stop reason ${reason} shows its label`, async ({ page }) => {
      await open(page)
      await tick(page).check()
      await ask(page, `scenario ${kw}`)
      const stop = page.getByTestId('stop-reason')
      await expect(stop).toHaveAttribute('data-stop', reason)
      await expect(stop).toContainText(label)
    })
  }

  test('stop button ends a stream mid-way', async ({ page }) => {
    await open(page)
    await tick(page).check()
    await streamQuery(
      page,
      [
        {
          text: sse([
            { ...RUN, event: 'step', node: 'router', n: 1, summary: 'Routing' },
            { ...RUN, event: 'token', text: 'Partial ' },
          ]),
        },
      ],
      { hold: true },
    )
    await ask(page, 'slow one')
    await expect(page.getByTestId('answer')).toContainText('Partial')
    await page.getByRole('button', { name: 'Stop' }).click()
    await expect(page.getByTestId('stop-reason')).toHaveText('Stopped by you')
    await expect(page.getByRole('textbox', { name: 'Your question' })).toBeEnabled()
  })

  test('429 shows the rate-limit message', async ({ page, allow }) => {
    allow.console.push(/429|Failed to load resource/)
    await open(page)
    await tick(page).check()
    await failNext(page, 'post', '*/v1/query', 429, 'rate_limited')
    await ask(page, 'hi')
    await expect(page.getByRole('alert')).toContainText('Too many requests')
  })

  test('token_expired once: silent re-mint, request succeeds, no error shown', async ({
    page,
    allow,
  }) => {
    allow.console.push(/401|Failed to load resource/)
    await open(page)
    await tick(page).check()
    const mints: string[] = []
    page.on('request', (r) => {
      if (r.url().endsWith('/v1/auth/anonymous')) mints.push(r.url())
    })
    await failNext(page, 'post', '*/v1/query', 401, 'token_expired', true)
    await ask(page, 'hello')
    await expect(page.getByTestId('answer')).toContainText('Revenue was 4.2 million')
    await expect(page.getByRole('alert')).toHaveCount(0)
    expect(mints).toHaveLength(1)
  })
})

test.describe('sessions', () => {
  test('new chat appears in the list; switching loads history', async ({ page }) => {
    await open(page)
    await tick(page).check()
    await ask(page, 'hello')
    await expect(page.getByTestId('stop-reason')).toBeVisible()
    const list = page.getByRole('region', { name: 'Conversations' })
    await expect(list.getByRole('button', { name: /New conversation/ })).toBeVisible()
    await list.getByRole('button', { name: /What was revenue\?/ }).click()
    await expect(page).toHaveURL(/\/chat\/sess-1$/)
    await expect(page.getByRole('log', { name: 'Conversation' })).toContainText(
      'Revenue was 4.2 million.',
    )
  })

  test('each conversation restores its own scope', async ({ page, allow }) => {
    // The mock has no GET for sessions it just created: returns 404 ("Conversation not found").
    allow.console.push(/404/)
    await open(page)
    await tick(page).check()
    await ask(page, 'hello')
    await expect(page.getByTestId('stop-reason')).toBeVisible()
    const list = page.getByRole('region', { name: 'Conversations' })
    await list.getByRole('button', { name: /What was revenue\?/ }).click()
    await expect(page).toHaveURL(/\/chat\/sess-1$/)
    await tick(page).uncheck()
    await expect(tick(page)).not.toBeChecked()
    await list.getByRole('button', { name: /New conversation/ }).click()
    await expect(page).toHaveURL(/\/chat\/sess-new-/)
    await expect(tick(page)).toBeChecked()
    await list.getByRole('button', { name: /What was revenue\?/ }).click()
    await expect(tick(page)).not.toBeChecked()
  })
})

test.describe('narrow viewport', () => {
  test.use({ viewport: { width: 800, height: 900 } })

  test('one pane at a time with tabs; citation jumps to Source', async ({ page }) => {
    await page.goto('/documents')
    await page.waitForFunction(() => window.__mock !== undefined)
    const tabs = page.getByRole('tablist', { name: 'Panes' })
    await expect(tabs).toBeVisible()
    await expect(tick(page)).toBeVisible()
    await expect(page.getByRole('textbox', { name: 'Your question' })).toHaveCount(0)
    await tick(page).check()
    await tabs.getByRole('tab', { name: 'Chat' }).click()
    await expect(tick(page)).toHaveCount(0)
    await ask(page, 'revenue?')
    await expect(page.getByTestId('answer')).toBeVisible()
    await page.getByRole('button', { name: /annual-report\.pdf · p\.1/ }).click()
    await expect(tabs.getByRole('tab', { name: 'Source' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('mark')).toHaveCount(1)
  })
})

test.describe('hostile payloads', () => {
  test('answer, step and filename render as literal text', async ({ page }) => {
    const raw = '<img src=x onerror=window.__pwned=1><script>window.__pwned=2</script>'
    await open(page)
    await tick(page).check()
    await streamQuery(page, [
      {
        text: sse([
          { ...RUN, event: 'step', node: 'router', n: 1, summary: raw },
          { ...RUN, event: 'token', text: raw },
          {
            ...RUN,
            event: 'final',
            answer: raw,
            citations: [],
            abstained: false,
            stopped_reason: 'done',
            usage: { steps: 1, tokens: 1, usd: 0 },
          },
        ]),
      },
    ])
    await ask(page, 'attack')
    const answer = page.getByTestId('answer')
    await expect(answer).toHaveText(raw)
    expect(
      await page.locator('[data-testid=assistant] img, [data-testid=assistant] script').count(),
    ).toBe(0)
    expect(await answer.evaluate((el) => el.children.length)).toBe(0)
    await page.waitForTimeout(200)
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined()
  })
})

test.describe('csp', () => {
  test('mock-variant CSP header is served and enforced in the browser', async ({ page, allow }) => {
    const res = await page.goto('/chat')
    const csp = res!.headers()['content-security-policy']
    expect(csp).toContain("script-src 'self'")
    expect(csp).toContain("worker-src 'self'")
    expect(csp).not.toContain('unsafe-inline')
    await page.waitForFunction(() => window.__mock !== undefined)
    // An injected inline script must be blocked and reported (proves enforcement).
    allow.console.push(/Content Security Policy|Content-Security-Policy/)
    await page.evaluate(() => {
      const s = document.createElement('script')
      s.textContent = 'window.__pwned = 1'
      document.body.appendChild(s)
    })
    await expect.poll(() => page.evaluate(() => window.__csp?.length)).toBe(1)
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined()
    await page.evaluate(() => (window.__csp = [])) // expected violation consumed
  })
})
