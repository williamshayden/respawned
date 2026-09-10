import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { connect, mockEngine, navigate } from './mock-engine'
import { createFixtures } from '../src/data/fixtures'

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

test('selecting another record starts at its heading without resetting scroll while editing', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 700 })
  const detail = page.locator('.review-scroll')
  await detail.hover()
  await page.mouse.wheel(0, 2000)
  await expect.poll(() => detail.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  const copy = 'Hi Maya, is there an update on the next interview?'
  await page.getByRole('textbox', { name: 'Draft message', exact: true }).fill(copy)
  await expect(page.getByText('Unsaved changes', { exact: true })).toBeVisible()
  await expect.poll(() => detail.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  await row(page, 'Jordan Ellis').click()
  await expect(selectedPanel(page).getByRole('heading', { name: 'Jordan Ellis', exact: true })).toBeInViewport({ ratio: 1 })
  await expect.poll(() => detail.evaluate(element => element.scrollTop)).toBe(0)
  await row(page, 'Maya Chen').click()
  await expect(selectedPanel(page).getByRole('heading', { name: 'Maya Chen', exact: true })).toBeInViewport({ ratio: 1 })
  await expect(page.getByRole('textbox', { name: 'Draft message', exact: true })).toHaveValue(copy)
  await page.screenshot({ path: testInfo.outputPath('record-selection-scroll.png') })
})

test('approval produces one unsent outbox entry and an accurate CSV export', async ({ page }) => {
  const copy = await page.getByRole('textbox', { name: 'Draft message' }).inputValue()
  const serverCsv = `id,draft_id,contact_key,contact_address,contact_name,channel,opportunity_ids,body,status,authorization_mode,created_at,sent_at\r\n1,server-draft,server-contact,maya@northstar.example,Maya,email,[],"${copy}",pending,human,2026-09-09T12:00:00Z,\r\n`
  await page.route('**/v1/ui/outbox/export?**', async route => {
    expect(route.request().headers().authorization).toBe('Bearer browser-test-operator-token')
    expect(new URL(route.request().url()).searchParams.get('workspace_id')).toBe('job_application')
    await route.fulfill({ contentType: 'text/csv; charset=utf-8', body: serverCsv })
  })
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
  expect(csv).toBe(serverCsv)
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
  await expect(page.getByRole('heading', { name: 'Activity', level: 1, exact: true })).toBeVisible()
  await navigate(page, 'Policy')
  await expect(page.getByRole('heading', { name: 'Effective policy' })).toBeVisible()
  await expect(page.getByText('48 hours', { exact: true })).toBeVisible()
})

test('a live request failure during export keeps the unsent reservation visible', async ({ page }) => {
  await page.getByRole('button', { name: 'Approve to outbox' }).click()
  await expect(page.getByRole('status')).toContainText('Added to outbox')
  await navigate(page, 'Outbox')
  const downloads: string[] = []
  page.on('download', item => downloads.push(item.suggestedFilename()))
  await page.route('**/v1/ui/outbox/export?**', route => route.fulfill({
    status: 503, json: { detail: 'Database unavailable' },
  }))
  await page.getByRole('button', { name: 'Export CSV', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveText('Database unavailable')
  await expect(page.getByRole('button', { name: 'Export CSV', exact: true })).toBeEnabled()
  await expect(page.getByText('Unsent', { exact: true })).toBeVisible()
  expect(downloads).toEqual([])
})

test('a live request failure stays live and never replaces records with the demo', async ({ page }) => {
  await page.route('**/v1/ui/config', (route) => route.fulfill({ status: 200, json: { policy_mode: 'human', cooldown_hours: 48, max_draft_characters: 320, source_freshness: 'unknown' } }))
  await page.route('**/v1/ui/records?*', (route) => route.fulfill({ status: 503, json: { detail: 'Live records are temporarily unavailable.' } }))
  await page.route('**/v1/ui/outbox?*', (route) => route.fulfill({ status: 200, json: { items: [], has_more: false } }))
  await page.route('**/v1/ui/inbox?*', (route) => route.fulfill({ status: 200, json: { items: [], total: 0, has_more: false, as_of: '2026-09-09T14:00:00Z', source_freshness: 'unknown', outreach_eligibility: 'not_evaluated' } }))
  await page.reload()
  await page.getByLabel('Engine access token', { exact: true }).fill('fictional-test-operator-token')
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

test('source messages can be read before approval with keyboard access and preserved text', async ({ page }, testInfo) => {
  const source = 'Hi Jordan,\n\n  Please send the revised portfolio.\n<img src=x onerror=alert(1)>\nhttps://source.example/' + 'x'.repeat(160)
  const records = createFixtures().filter(record => record.kind === 'job_application')
  const target = records.find(record => record.id === 'job-northstar-backend')!
  target.activities[0] = { ...target.activities[0], type: 'note_added', summary: 'Interview completed.' }
  target.activities[1] = { ...target.activities[1], type: 'contact_replied', summary: source, source_url: 'https://source.example/message/42' }
  target.activities[2] = { ...target.activities[2], summary: null }
  await page.route('**/v1/ui/records?*', route => route.fulfill({ json: { items: records, total: records.length, has_more: false, as_of: '2026-09-10T12:00:00Z' } }))
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  const activity = selectedPanel(page).locator('.timeline li').filter({ hasText: 'Recruiter replied' })
  const disclosure = activity.locator('details')
  const summary = activity.getByText('View message', { exact: true })
  await expect(disclosure).not.toHaveAttribute('open')
  await expect(activity.locator('p')).not.toBeVisible()
  await expect(selectedPanel(page).locator('.activity-message')).toHaveCount(2)
  await expect(selectedPanel(page).getByText('View activity', { exact: true })).toBeVisible()
  await summary.focus()
  await page.keyboard.press('Enter')
  await expect(disclosure).toHaveAttribute('open', '')
  await expect(activity.locator('p')).toBeVisible()
  expect(await activity.locator('p').textContent()).toBe(source)
  await expect(activity.locator('img')).toHaveCount(0)
  await expect(activity.getByRole('link', { name: 'Source for Recruiter replied' })).toHaveAttribute('href', 'https://source.example/message/42')
  await activity.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('source-message-desktop.png') })
  await row(page, 'Maya Chen').click()
  await page.setViewportSize({ width: 390, height: 844 })
  await activity.scrollIntoViewIfNeeded()
  await expect(activity.locator('p')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(await activity.locator('p').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  expect((await activity.locator('p').boundingBox())!.width).toBeGreaterThan(250)
  await page.screenshot({ path: testInfo.outputPath('source-message-mobile.png') })
  await summary.focus()
  await page.keyboard.press('Enter')
  await expect(disclosure).not.toHaveAttribute('open')
})
