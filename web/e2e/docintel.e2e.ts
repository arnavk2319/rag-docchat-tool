import { expect, test, failNext, open } from './fixtures'

// The e2e build bakes VITE_FEATURE_DOC_INTEL=1 (see build:e2e).
const tick = (page: import('@playwright/test').Page) =>
  page.getByRole('checkbox', { name: /annual-report/ })

test.describe('doc-intel', () => {
  test('nav tabs reach each view', async ({ page }) => {
    await open(page, '/chat')
    const nav = page.getByRole('navigation', { name: 'Views' })
    for (const name of ['Summary', 'Sentiment', 'Compare']) {
      await nav.getByRole('link', { name }).click()
      await expect(page.getByRole('heading', { name, level: 2 })).toBeVisible()
    }
  })

  test('summary: explicit run, citation opens the source passage', async ({ page }) => {
    await open(page, '/summary')
    await expect(page.getByText('Uses your daily budget')).toBeVisible()
    const run = page.getByRole('button', { name: 'Run summary' })
    await expect(run).toBeDisabled()
    await tick(page).check()
    await run.click()
    await expect(page.getByText(/Summary of annual-report.pdf/)).toBeVisible()
    await page.getByRole('button', { name: /annual-report.pdf · p.1/ }).click()
    await expect(page.getByRole('complementary', { name: 'Source passage' })).toContainText(
      'Revenue was 4.2 million',
    )
  })

  test('sentiment shows label and confidence', async ({ page }) => {
    await open(page, '/sentiment')
    await tick(page).check()
    await page.getByRole('button', { name: 'Run sentiment' }).click()
    await expect(page.getByText('positive', { exact: true })).toBeVisible()
    await expect(page.getByText('Confidence 82%')).toBeVisible()
  })

  test('compare needs two documents', async ({ page }) => {
    await open(page, '/compare')
    await page
      .getByLabel('Upload documents')
      .setInputFiles([{ name: 'second.txt', mimeType: 'text/plain', buffer: Buffer.from('x y z') }])
    await tick(page).check()
    const run = page.getByRole('button', { name: 'Run compare' })
    await expect(run).toBeDisabled()
    await page.getByRole('checkbox', { name: /second\.txt/ }).check()
    await run.click()
    await expect(page.getByText('Both documents report revenue.')).toBeVisible()
    await expect(page.getByText(/covers 2025 only/)).toBeVisible()
  })

  test('429 spend cap maps to a message', async ({ page, allow }) => {
    allow.console.push(/429|Failed to load resource/)
    await open(page, '/summary')
    await failNext(page, 'post', '*/v1/documents/:id/summary', 429, 'spend_cap_exceeded')
    await tick(page).check()
    await page.getByRole('button', { name: 'Run summary' }).click()
    await expect(page.getByRole('alert')).toContainText('daily demo spend limit')
    await expect(page.getByRole('button', { name: 'Run summary' })).toBeEnabled()
  })

  test('Chat -> Summary -> Chat keeps the draft', async ({ page }) => {
    await open(page, '/chat')
    await tick(page).check()
    const box = page.getByRole('textbox', { name: 'Your question' })
    await box.fill('half typed')
    const nav = page.getByRole('navigation', { name: 'Views' })
    await nav.getByRole('link', { name: 'Summary' }).click()
    await expect(page.getByRole('heading', { name: 'Summary', level: 2 })).toBeVisible()
    await nav.getByRole('link', { name: 'Chat' }).click()
    await expect(box).toHaveValue('half typed')
  })
})
