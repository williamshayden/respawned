import { expect, test, type Page } from '@playwright/test'
import { connect, mockEngine, navigate } from './mock-engine'
import { createDemoClient } from '../src/data/demoClient'
import { CONNECTIONS_STORAGE_KEY } from '../src/data/connections'

const REMOTE = 'https://remote-engine.example'
const REMOTE_TOKEN = 'remote-only-test-token'

async function engines(page: Page) {
  await mockEngine(page)
  const remote = createDemoClient(null)
  const mutations: { path: string; body: unknown }[] = []
  await page.route(`${REMOTE}/**`, async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname.slice('/v1/ui'.length)
    const headers = { 'Access-Control-Allow-Origin': 'http://127.0.0.1:5173', 'Access-Control-Allow-Headers': 'Authorization,Content-Type', 'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS' }
    if (request.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers }); return }
    expect(request.headers().authorization).toBe(`Bearer ${REMOTE_TOKEN}`)
    expect(request.headers().cookie).toBeUndefined()
    const body = request.postDataJSON()
    if (request.method() !== 'GET') mutations.push({ path, body })
    let json: unknown
    if (path === '/setup') json = { database: { status: 'ready', message: 'Remote database ready' }, review: { enabled: true, authentication: 'bearer', token_env: 'RESPAWNED_REVIEW_TOKEN' }, model: { backend: 'openai_compatible', base_url: 'https://model.example/v1', model_alias: 'remote-model', timeout_seconds: 60, api_key_env: 'RESPAWNED_MODEL_API_KEY', key_configured: true, ready: true, verified: false, source: 'saved', error: null }, outbox: { mode: 'export_only', automatic_delivery: false }, sources: { mode: 'api_import' } }
    else if (path === '/config') json = await remote.config()
    else if (path === '/workspaces') json = { items: [], available_kinds: [], scope: 'shared_engine' }
    else if (path === '/records') {
      const records = await remote.listRecords()
      json = { ...records, items: records.items.map(record => ({ ...record, title: `Remote ${record.title}` })) }
    } else if (path === '/inbox') json = await remote.listInbox()
    else if (path === '/outbox') json = { items: await remote.listOutbox(), has_more: false }
    else if (path.startsWith('/records/')) json = await remote.getRecord(decodeURIComponent(path.slice(9)))
    else if (path.startsWith('/drafts/')) {
      const [, , encodedId, action] = path.split('/')
      const id = decodeURIComponent(encodedId)
      if (action === 'edit') json = await remote.edit(id, body.body, body.review_token)
      else if (action === 'approve') json = await remote.approve(id, body.review_token)
      else json = await remote.reject(id, body.review_token)
    } else throw new Error(`Unexpected remote operation ${path}`)
    await route.fulfill({ json, headers })
  })
  await page.goto('/')
  await connect(page, 'job_application')
  await navigate(page, 'Connections')
  await page.getByLabel('Engine name', { exact: true }).fill('Remote staging')
  await page.getByLabel('Engine URL', { exact: true }).fill(REMOTE)
  await page.getByLabel('Engine access token', { exact: true }).fill(REMOTE_TOKEN)
  await page.getByRole('button', { name: 'Connect engine', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Engine connected.')
  return { remote, mutations }
}

test('keeps same-ID drafts bound to the selected engine and remembers no remote credentials', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const { remote, mutations } = await engines(page)
  await navigate(page, 'Review queue')
  const copy = 'Local unsaved draft belongs only to this engine.'
  await page.getByRole('textbox', { name: 'Draft message', exact: true }).fill(copy)
  await page.getByLabel('Engine', { exact: true }).selectOption({ label: 'Remote staging' })
  await expect(page.getByRole('alert')).toContainText('Save or discard')
  await expect(page.getByLabel('Engine', { exact: true })).toHaveValue('local')
  await expect(page.getByRole('textbox', { name: 'Draft message', exact: true })).toHaveValue(copy)
  expect(mutations).toEqual([])
  await page.getByRole('button', { name: 'Discard changes', exact: true }).click()
  await page.getByLabel('Engine', { exact: true }).selectOption({ label: 'Remote staging' })
  await expect(page.getByRole('heading', { name: 'Review queue', exact: true })).toBeVisible()
  await expect(page.locator('.record-row').filter({ hasText: 'Remote Workspace renewal' })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Draft message', exact: true })).not.toHaveValue(copy)
  await page.getByRole('button', { name: 'Approve to outbox', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Added to outbox')
  expect(mutations).toHaveLength(1)
  expect(decodeURIComponent(mutations[0].path)).toBe('/drafts/draft:job-northstar-backend/approve')
  expect(await remote.listOutbox()).toHaveLength(1)
  await page.getByLabel('Engine', { exact: true }).selectOption('local')
  await expect(page.getByRole('button', { name: 'Approve to outbox', exact: true })).toBeEnabled()
  await page.reload()
  await navigate(page, 'Connections')
  const card = page.locator('.connection-card').filter({ has: page.getByRole('heading', { name: 'Remote staging', exact: true }) })
  await expect(card.getByText('Locked', { exact: true })).toBeVisible()
  const storage = await page.evaluate(() => ({ local: JSON.stringify(localStorage), session: JSON.stringify(sessionStorage) }))
  expect(storage.local).toContain(REMOTE)
  expect(storage.local + storage.session).not.toContain(REMOTE_TOKEN)
  await card.getByRole('button', { name: 'Remove connection to Remote staging', exact: true }).click()
  await expect(card).toHaveCount(0)
  expect(await page.evaluate(key => localStorage.getItem(key), CONNECTIONS_STORAGE_KEY)).not.toContain(REMOTE)
  expect(await remote.listOutbox()).toHaveLength(1)
  expect(errors).toEqual([])
})

test('holds the active engine while a workspace write is pending', async ({ page }) => {
  await engines(page)
  await page.getByRole('button', { name: 'Manage workspaces', exact: true }).click()
  let finish!: () => void
  const gate = new Promise<void>(resolve => { finish = resolve })
  let started!: () => void
  const pending = new Promise<void>(resolve => { started = resolve })
  await page.route('http://127.0.0.1:5173/v1/ui/workspaces', async route => {
    if (route.request().method() !== 'POST') { await route.fallback(); return }
    started()
    await gate
    await route.fulfill({ json: { id: 'saved-local-workspace', ...route.request().postDataJSON(), created_at: '', updated_at: '' } })
  })
  await page.getByLabel('Workspace name', { exact: true }).fill('Local saved collection')
  await page.getByRole('button', { name: 'Save workspace', exact: true }).click()
  await pending
  await navigate(page, 'Connections')
  await expect(page.getByRole('heading', { name: 'Workspaces', exact: true })).toBeVisible()
  await page.getByLabel('Engine', { exact: true }).selectOption({ label: 'Remote staging' })
  await expect(page.getByLabel('Engine', { exact: true })).toHaveValue('local')
  await expect(page.getByRole('alert')).toContainText('Finish the current action')
  finish()
  await expect(page.getByRole('status')).toContainText('Workspace saved')
  await page.getByLabel('Engine', { exact: true }).selectOption({ label: 'Remote staging' })
  await expect(page.locator('.record-row').filter({ hasText: 'Remote Workspace renewal' })).toBeVisible()
  await expect(page.getByLabel('Workspace', { exact: true })).toHaveValue('all')
})

test('mobile connection setup can scroll to the form and remote server instructions', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await engines(page)
  const connectButton = page.getByRole('button', { name: 'Connect engine', exact: true })
  await connectButton.scrollIntoViewIfNeeded()
  await expect(connectButton).toBeInViewport({ ratio: 1 })
  await page.getByText('Prepare a remote engine', { exact: true }).click()
  const originSetting = page.locator('.connection-server-help pre')
  await originSetting.scrollIntoViewIfNeeded()
  await expect(originSetting).toBeInViewport({ ratio: 1 })
  await expect(originSetting).toContainText('RESPAWNED_UI_ORIGINS=http://127.0.0.1:5173')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})
