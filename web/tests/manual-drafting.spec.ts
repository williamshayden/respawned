import { expect, test, type Page } from '@playwright/test'
import { CONNECTIONS_STORAGE_KEY } from '../src/data/connections'
import { connect, mockEngine, navigate } from './mock-engine'

const row = (page: Page, name: string) => page.locator('.record-row').filter({ hasText: name })
const copyBox = (page: Page) => page.getByRole('textbox', { name: 'Draft message', exact: true })

test('starts at an empty review queue and reaches an unsent outbox without a model or queue sync', async ({ page }) => {
  const { client, writes } = await mockEngine(page, { canonical: true, localSession: true, empty: true })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Review queue', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Start with one record', exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Find a record', exact: true })).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'Channel', exact: true })).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'Sort records', exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'No record selected', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Import records', exact: true })).toHaveCount(1)
  await expect(page.getByLabel('Engine', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Workspace', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Manage workspaces', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Import records', exact: true }).click()
  await expect(page.getByLabel('Or paste your import JSON')).toBeInViewport()
  await expect(page.getByLabel('API base URL')).not.toBeVisible()
  await page.getByLabel('Or paste your import JSON').fill(JSON.stringify({ opportunities: [{ id: 'job-aster-platform' }], activities: [] }))
  await page.getByRole('button', { name: 'Import records', exact: true }).click()
  await page.getByRole('button', { name: 'Review imported records', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Evaluate queue', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Write draft', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Save draft', exact: true })).toBeDisabled()
  const body = 'Hi Jordan, Thursday afternoon works for me. Would 2 pm suit you?'
  await copyBox(page).fill(body)
  await expect(page.getByRole('button', { name: 'Approve to outbox', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Save draft', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Draft saved. Review it before approval.')
  const draft = (await client.getRecord('job-aster-platform')).draft!
  expect(draft.body).toBe(body)
  expect(await client.listOutbox()).toEqual([])
  expect(writes).toEqual([
    { path: '/import', body: { opportunities: [{ id: 'job-aster-platform' }], activities: [] } },
    { path: '/records/job-aster-platform/draft', body: { body } },
  ])
  await page.getByRole('button', { name: 'Approve to outbox', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Added to outbox. Your message is unsent.')
  expect(writes.at(-1)).toEqual({ path: `/drafts/${encodeURIComponent(draft.id)}/approve`, body: { review_token: draft.review_token } })
  expect(await client.listOutbox()).toEqual([expect.objectContaining({ body, status: 'pending', sent_at: null })])
  await navigate(page, 'Outbox')
  await expect(page.locator('.outbox-item')).toContainText(body)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Review queue', exact: true })).toBeVisible()
  expect(writes).toHaveLength(3)
})

test('keeps unsaved manual copy per record and blocks an engine switch', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.addInitScript(key => localStorage.setItem(key, JSON.stringify([{ id: 'other-engine', name: 'Other engine', baseUrl: 'https://other-engine.example' }])), CONNECTIONS_STORAGE_KEY)
  const { writes } = await mockEngine(page, { canonical: true, localSession: true })
  await page.goto('/')
  await row(page, 'Jordan Ellis').click()
  await page.getByRole('button', { name: 'Write draft', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Save draft', exact: true })).toBeInViewport({ ratio: 1 })
  const scroll = await page.locator('.review-scroll').evaluate(element => element.scrollTop)
  const body = 'Hi Jordan, Thursday afternoon works for me.'
  await copyBox(page).fill(body)
  expect(await page.locator('.review-scroll').evaluate(element => element.scrollTop)).toBe(scroll)
  await row(page, 'Taylor Brooks').click()
  await page.getByRole('button', { name: 'Write draft', exact: true }).click()
  await copyBox(page).fill('Hi Taylor, is there an update on next steps?')
  await row(page, 'Jordan Ellis').click()
  await expect(copyBox(page)).toHaveValue(body)
  await expect(page.getByRole('button', { name: 'Save draft', exact: true })).toBeInViewport({ ratio: 1 })
  await page.getByLabel('Engine', { exact: true }).selectOption('other-engine')
  await expect(page.getByRole('alert')).toContainText('Save or discard')
  await expect(page.getByLabel('Engine', { exact: true })).toHaveValue('local')
  await expect(copyBox(page)).toHaveValue(body)
  expect(writes).toEqual([])
})

test('keeps invalid or rejected manual copy without approval or model fallback', async ({ page }) => {
  const { client, writes } = await mockEngine(page, { canonical: true, localSession: true })
  await page.goto('/')
  await row(page, 'Jordan Ellis').click()
  await page.getByRole('button', { name: 'Write draft', exact: true }).click()
  await copyBox(page).fill('x'.repeat(321))
  await expect(page.getByRole('button', { name: 'Save draft', exact: true })).toBeDisabled()
  expect(writes).toEqual([])
  const submitted: unknown[] = []
  await page.route('**/v1/workflow/records/job-aster-platform/draft', route => {
    submitted.push(route.request().postDataJSON())
    return route.fulfill({ status: 422, json: { detail: 'Replace the unresolved recipient placeholder.' } })
  })
  const invalid = 'Hi [NAME], when would you like to speak?'
  await copyBox(page).fill(invalid)
  await page.getByRole('button', { name: 'Save draft', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Replace the unresolved recipient placeholder.')
  await expect(copyBox(page)).toHaveValue(invalid)
  await expect(page.getByRole('button', { name: 'Approve to outbox', exact: true })).toBeDisabled()
  expect((await client.getRecord('job-aster-platform')).draft).toBeUndefined()
  expect(await client.listOutbox()).toEqual([])
  expect(submitted).toEqual([{ body: invalid }])
  expect(writes).toEqual([])
})

test('keeps a competing server draft separate from unsaved manual copy', async ({ page }) => {
  const { client, writes } = await mockEngine(page, { canonical: true, localSession: true })
  await page.goto('/')
  await row(page, 'Jordan Ellis').click()
  await page.getByRole('button', { name: 'Write draft', exact: true }).click()
  const local = 'My unsaved message for Jordan.'
  const saved = 'The draft saved in another session.'
  await copyBox(page).fill(local)
  const existing = await client.draft('job-aster-platform', saved)
  await page.getByRole('button', { name: 'Save draft', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('A different draft already exists')
  await expect(copyBox(page)).toHaveValue(local)
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(page.getByText('A saved draft is now available', { exact: true })).toBeVisible()
  await expect(copyBox(page)).toHaveValue(local)
  await expect(page.getByRole('button', { name: 'Save draft', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Approve to outbox', exact: true })).toBeDisabled()
  await page.getByText('View saved draft', { exact: true }).click()
  await expect(page.locator('.manual-draft-conflict')).toContainText(saved)
  expect(writes).toEqual([{ path: '/records/job-aster-platform/draft', body: { body: local } }])
  expect((await client.getRecord('job-aster-platform')).draft).toEqual(existing)
  await page.getByRole('button', { name: 'Discard draft', exact: true }).click()
  await expect(copyBox(page)).toHaveValue(saved)
  await expect(page.getByRole('button', { name: 'Approve to outbox', exact: true })).toBeEnabled()
})

test('keeps legacy engine drafting available without offering manual creation', async ({ page }) => {
  const { writes } = await mockEngine(page)
  await page.goto('/')
  await connect(page, 'job_application')
  await row(page, 'Jordan Ellis').click()
  await expect(page.getByRole('button', { name: 'Write draft', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Generate draft', exact: true }).click()
  await expect(copyBox(page)).toHaveValue(/Hi Jordan/)
  expect(writes).toEqual([{ path: '/records/job-aster-platform/draft', body: null }])
})

test('a delayed local session does not override deliberate navigation', async ({ page }) => {
  await mockEngine(page, { canonical: true, localSession: true })
  let finish!: () => void
  const gate = new Promise<void>(resolve => { finish = resolve })
  let started!: () => void
  const pending = new Promise<void>(resolve => { started = resolve })
  await page.route('**/v1/workflow/session', async route => { started(); await gate; await route.fulfill({ json: { authenticated: true } }) })
  await page.goto('/')
  await pending
  await navigate(page, 'Connections')
  await expect(page.getByRole('heading', { name: 'Connections', exact: true })).toBeVisible()
  finish()
  await expect(page.getByRole('button', { name: 'Engine connected', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Connections', exact: true })).toBeVisible()
})

test('keeps an empty saved workspace and a failed refresh separate from the first-record welcome', async ({ page }) => {
  await mockEngine(page, { canonical: true, localSession: true, empty: true })
  await page.route('**/v1/workflow/workspaces', route => route.fulfill({ json: {
    items: [{ id: 'saved-empty', name: 'Empty workspace', description: '', kinds: ['generic'], created_at: '', updated_at: '' }],
    available_kinds: ['generic'], scope: 'shared_engine',
  } }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Start with one record', exact: true })).toBeVisible()
  await page.getByLabel('Workspace', { exact: true }).selectOption('saved-empty')
  await expect(page.getByRole('heading', { name: 'No tracked records', exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Find a record', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Start with one record', exact: true })).toHaveCount(0)
  await page.getByLabel('Workspace', { exact: true }).selectOption('all')
  await expect(page.getByRole('heading', { name: 'Start with one record', exact: true })).toBeVisible()
  await page.route('**/v1/workflow/records?*', route => route.fulfill({ status: 503, json: { detail: 'The database is unavailable.' } }))
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('The database is unavailable.')
  await expect(page.getByRole('heading', { name: 'Start with one record', exact: true })).toHaveCount(0)
})
