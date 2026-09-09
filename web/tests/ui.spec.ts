import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { connect, mockEngine, navigate } from './mock-engine'

const selectedPanel = (page: Page) => page.getByRole('region', { name: 'Selected record' })
const row = (page: Page, text: string) => page.locator('.record-row').filter({ hasText: text })
const browserErrors = new WeakMap<Page, { runtime: string[]; console: string[] }>()

test.beforeEach(async ({ page }) => {
  const errors = { runtime: [] as string[], console: [] as string[] }
  browserErrors.set(page, errors)
  page.on('pageerror', (error) => errors.runtime.push(error.message))
  page.on('console', (message) => { if (['error', 'warning'].includes(message.type())) errors.console.push(message.text()) })
  await mockEngine(page)
  await page.goto('/')
  await connect(page, 'job_application')
  await expect(page.getByRole('heading', { name: 'Review queue', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Engine connected', exact: true, includeHidden: true })).toBeAttached()
})

test.afterEach(async ({ page }, testInfo) => {
  expect(browserErrors.get(page)?.runtime ?? []).toEqual([])
  // The live-failure test deliberately returns HTTP 503, which Chromium logs.
  if (!testInfo.title.startsWith('a live request failure')) expect(browserErrors.get(page)?.console ?? []).toEqual([])
})

test('keeps the review structure while job and sales fields adapt', async ({ page }, testInfo) => {
  await expect(page).toHaveTitle('Respawned')
  await expect(selectedPanel(page).getByRole('heading', { name: 'Maya Chen', exact: true })).toBeVisible()
  await expect(selectedPanel(page).locator('dt').filter({ hasText: /^Value$/ })).toHaveCount(0)
  await page.setViewportSize({ width: 1586, height: 992 })
  await page.screenshot({ path: testInfo.outputPath('desktop-jobs.png') })
  await page.getByLabel('Workspace', { exact: true }).selectOption('sales')
  await expect(selectedPanel(page).getByRole('heading', { name: 'Alex Morgan', exact: true })).toBeVisible()
  await expect(selectedPanel(page).locator('dt').filter({ hasText: /^Value$/ })).toBeVisible()
  await expect(selectedPanel(page).getByText('$12,000', { exact: true })).toBeVisible()
  await expect(selectedPanel(page).getByRole('heading', { name: 'Why this follow-up' })).toBeVisible()
  await expect(selectedPanel(page).getByRole('heading', { name: 'Recent activity' })).toBeVisible()
  await expect(selectedPanel(page).getByRole('heading', { name: 'Draft message' })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('desktop-sales.png') })
  await page.getByLabel('Workspace', { exact: true }).selectOption('job_application')
  await expect(selectedPanel(page).getByRole('heading', { name: 'Maya Chen', exact: true })).toBeVisible()
  await expect(page.locator('.record-value')).toHaveCount(0)
  await expect(selectedPanel(page).locator('dt').filter({ hasText: /^Value$/ })).toHaveCount(0)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
})

test('unknown record kinds use supplied context fields and the same draft flow', async ({ page }) => {
  await page.getByLabel('Workspace', { exact: true }).selectOption('community_partnership')
  await expect(selectedPanel(page).getByRole('heading', { name: 'Robin Bell', exact: true })).toBeVisible()
  await expect(selectedPanel(page).getByText('Common Ground', { exact: true })).toBeVisible()
  await expect(selectedPanel(page).getByText('Community workshop', { exact: true })).toBeVisible()
  await selectedPanel(page).getByRole('button', { name: 'Generate draft' }).click()
  await expect(page.getByRole('textbox', { name: 'Draft message' })).toHaveValue(/Hi Robin/)
  await expect(page.getByRole('button', { name: 'Approve to outbox' })).toBeEnabled()
})

test('a contactless role remains tracked and cannot create its own outreach', async ({ page }) => {
  await page.getByRole('button', { name: 'All tracked', exact: true }).click()
  await row(page, 'Developer Platform Engineer').click()
  await expect(selectedPanel(page).getByRole('heading', { name: 'Developer Platform Engineer', exact: true })).toBeVisible()
  await expect(selectedPanel(page).getByText('Human contact not identified', { exact: true })).toBeVisible()
  await expect(selectedPanel(page).getByRole('button', { name: 'Generate draft' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Approve to outbox' })).toBeDisabled()
})

test('unsaved copy survives switching records, and saved copy survives reload', async ({ page }) => {
  const copy = 'Hi Maya, I wanted to check whether the team has shared an update on the Backend Engineer role.'
  await page.getByRole('textbox', { name: 'Draft message' }).fill(copy)
  await expect(page.getByRole('button', { name: 'Approve to outbox' })).toBeDisabled()
  await row(page, 'Jordan Ellis').click()
  await expect(selectedPanel(page).getByRole('heading', { name: 'Jordan Ellis', exact: true })).toBeVisible()
  await row(page, 'Maya Chen').click()
  await expect(page.getByRole('textbox', { name: 'Draft message' })).toHaveValue(copy)
  await page.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Approve to outbox' })).toBeEnabled()
  await page.reload()
  await connect(page, 'job_application')
  await expect(page.getByRole('textbox', { name: 'Draft message' })).toHaveValue(copy)
})

test('approval produces one unsent outbox entry and an accurate CSV export', async ({ page }) => {
  const copy = await page.getByRole('textbox', { name: 'Draft message' }).inputValue()
  await page.getByRole('button', { name: 'Approve to outbox' }).click()
  await expect(page.getByRole('status')).toContainText('Added to outbox')
  await navigate(page, 'Outbox')
  await expect(page.locator('.outbox-item')).toHaveCount(1)
  await expect(page.getByText('Unsent', { exact: true })).toBeVisible()
  await expect(page.locator('.message-copy')).toHaveText(copy)
  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export CSV', exact: true }).click()
  const download = await downloading
  expect(download.suggestedFilename()).toBe('respawned-outbox.csv')
  const file = await download.path()
  expect(file).not.toBeNull()
  const csv = await readFile(file!, 'utf8')
  expect(csv).toContain('maya@northstar.example')
  expect(csv).toContain('"pending"')
  expect(csv).toContain(copy)
  await navigate(page, 'Reply inbox')
  await expect(page.getByRole('button').filter({ hasText: 'Jordan Ellis' })).toBeVisible()
})

test('inbox shows human replies during cooldown and navigation resolves to the record', async ({ page }) => {
  await page.getByLabel('Workspace', { exact: true }).selectOption('sales')
  await navigate(page, 'Reply inbox')
  await page.getByRole('button').filter({ hasText: 'Devon Reed' }).click()
  await expect(selectedPanel(page).getByRole('heading', { name: 'Devon Reed', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve to outbox' })).toBeDisabled()
  await navigate(page, 'Activity')
  await expect(page.getByRole('heading', { name: 'Activity across your records' })).toBeVisible()
  await navigate(page, 'Policy')
  await expect(page.getByRole('heading', { name: 'Effective policy' })).toBeVisible()
  await expect(page.getByText('48 hours', { exact: true })).toBeVisible()
})

test('a live request failure stays live and never replaces records with the demo', async ({ page }) => {
  await page.route('**/v1/ui/config', (route) => route.fulfill({ status: 200, json: { policy_mode: 'human', cooldown_hours: 48, max_draft_characters: 320, source_freshness: 'unknown' } }))
  await page.route('**/v1/ui/records?*', (route) => route.fulfill({ status: 503, json: { detail: 'Live records are temporarily unavailable.' } }))
  await page.route('**/v1/ui/outbox?*', (route) => route.fulfill({ status: 200, json: { items: [], has_more: false } }))
  await page.route('**/v1/ui/inbox?*', (route) => route.fulfill({ status: 200, json: { items: [], total: 0, has_more: false, as_of: '2026-09-09T14:00:00Z', source_freshness: 'unknown', outreach_eligibility: 'not_evaluated' } }))
  await page.reload()
  await page.getByLabel('Review access token', { exact: true }).fill('fictional-test-operator-token')
  await page.getByRole('button', { name: 'Connect local engine', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Live records are temporarily unavailable.')
  await expect(page.getByRole('button', { name: 'Engine connected', exact: true })).toBeVisible()
  await expect(page.locator('.record-row')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Maya Chen', exact: true })).toHaveCount(0)
  const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(stored).not.toContain('fictional-test-operator-token')
})

test.describe('mobile review', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })

  test('moves between queue, detail and navigation without horizontal overflow', async ({ page }, testInfo) => {
    await expect(row(page, 'Maya Chen')).toBeVisible()
    await expect(selectedPanel(page)).toBeHidden()
    await row(page, 'Maya Chen').click()
    await expect(selectedPanel(page).getByRole('heading', { name: 'Maya Chen', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Back to records' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Approve to outbox' })).toBeInViewport()
    await page.screenshot({ path: testInfo.outputPath('mobile-detail.png') })
    const dimensions = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }))
    expect(dimensions.content).toBeLessThanOrEqual(dimensions.width)
    await page.getByRole('button', { name: 'Back to records' }).click()
    await expect(row(page, 'Maya Chen')).toBeVisible()
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click()
    await navigate(page, 'Policy')
    await expect(page.getByRole('heading', { name: 'Effective policy' })).toBeVisible()
  })
})
