import { expect, test, type Page } from '@playwright/test'
import { CONNECTIONS_STORAGE_KEY } from '../src/data/connections'
import { WATCH_STORAGE_KEY, watchKey, type Overview } from '../src/data/overview'

const remoteUrl = 'https://remote.respawned.test'
const remoteToken = 'overview-remote-access'
const workspaceId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
function summary(remote = false): Overview {
  return { generated_at: '2026-09-09T18:00:00Z', source_freshness: 'unknown', workspaces: [
    { id: 'all', name: 'All work', description: 'Every tracked conversation.', kinds: [], counts: { records: remote ? 912 : 503, ready: remote ? 75 : 280, pending_drafts: 12, reply_contacts: 8, pending_outbox: 2 } },
    { id: workspaceId, name: 'Partnerships', description: 'Workshops and ongoing collaborations.', kinds: ['partnership'], counts: { records: remote ? 68 : 31, ready: remote ? 22 : 9, pending_drafts: 4, reply_contacts: 3, pending_outbox: 1 } },
  ] }
}
async function environment(page: Page) {
  await page.addInitScript(({ storage, url }) => {
    localStorage.setItem(storage, JSON.stringify([{ id: 'remote', name: 'Studio engine', baseUrl: url }]))
  }, { storage: CONNECTIONS_STORAGE_KEY, url: remoteUrl })
  const requests: { engine: string; path: string; method: string; authorization: string | undefined }[] = []
  const state = { remoteStatus: 200, localReady: 280 }
  await page.route('**/v1/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const remote = url.origin === remoteUrl
    requests.push({ engine: remote ? 'remote' : 'local', path: url.pathname, method: request.method(), authorization: request.headers().authorization })
    const json = (payload: unknown, status = 200) => route.fulfill({ status, json: payload, headers: { 'Access-Control-Allow-Origin': '*' } })
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'authorization,content-type', 'Access-Control-Allow-Methods': 'GET' } })
    if (url.pathname === '/v1/ui/session') return json({ authenticated: true, local_launcher: true })
    if (url.pathname === '/v1/setup/bootstrap') return json({ review_enabled: true })
    if (remote && request.headers().authorization !== `Bearer ${remoteToken}`) return json({ detail: 'Access was not accepted' }, 401)
    if (url.pathname === '/v1/ui/overview') {
      if (remote && state.remoteStatus !== 200) return json({ detail: 'Engine unavailable' }, state.remoteStatus)
      const body = summary(remote)
      if (!remote) body.workspaces[0].counts.ready = state.localReady
      return json(body)
    }
    if (url.pathname === '/v1/ui/setup') return json({
      database: { status: 'ready', message: 'PostgreSQL is available' },
      review: { enabled: true, authentication: remote ? 'bearer' : 'local_session', token_env: 'RESPAWNED_REVIEW_TOKEN' },
      model: { backend: 'openai_compatible', source: 'environment', base_url: 'https://model.example/v1', model_alias: 'configured-model', timeout_seconds: 60, api_key_env: 'LITELLM_MASTER_KEY', key_configured: true, ready: true, verified: false, error: null },
      outbox: { mode: 'export_only', automatic_delivery: false, export_url: '/v1/ui/outbox/export' }, sources: { mode: 'api_import', import_url: '/v1/ui/import' },
    })
    if (url.pathname === '/v1/ui/config') return json({ policy_mode: 'human', cooldown_hours: 48, max_draft_characters: 320, source_freshness: 'unknown' })
    if (url.pathname === '/v1/ui/workspaces') return json({ items: [{ id: workspaceId, name: 'Partnerships', description: '', kinds: ['partnership'], created_at: '2026-09-09T18:00:00Z', updated_at: '2026-09-09T18:00:00Z' }], available_kinds: ['partnership'], scope: 'shared_engine' })
    if (url.pathname === '/v1/ui/records') return json({ items: [], total: 0, has_more: false, as_of: '2026-09-09T18:00:00Z' })
    if (url.pathname === '/v1/ui/outbox') return json({ items: [], has_more: false })
    if (url.pathname === '/v1/ui/inbox') return json({ items: [], total: 0, has_more: false, as_of: '2026-09-09T18:00:00Z', source_freshness: 'unknown', outreach_eligibility: 'not_evaluated' })
    return json({ detail: 'Unexpected overview request' }, 404)
  })
  return { state, requests }
}
async function navigate(page: Page, name: string) {
  if (await page.getByRole('button', { name: 'Open navigation', exact: true }).isVisible()) await page.getByRole('button', { name: 'Open navigation', exact: true }).click()
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name, exact: true }).click()
}
async function unlockRemote(page: Page) {
  await navigate(page, 'Connections')
  const card = page.locator('.connection-card').filter({ has: page.getByRole('heading', { name: 'Studio engine', exact: true }) })
  await card.getByRole('button', { name: 'Unlock', exact: true }).click()
  await card.getByLabel('Access token for Studio engine').fill(remoteToken)
  await card.getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(card.getByText('Unlocked', { exact: true })).toBeVisible()
  await navigate(page, 'Overview')
  await expect(page.locator('[data-engine-id="remote"]').getByText('Connected', { exact: true })).toBeVisible()
}

test('watches multiple engine workspaces with independent identities and opens the owning engine', async ({ page }, testInfo) => {
  const consoleErrors: string[] = []
  page.on('pageerror', error => consoleErrors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  const { requests } = await environment(page)
  await page.goto('/')
  await unlockRemote(page)
  await expect(page).toHaveTitle(/Respawned/)
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible()
  const local = page.locator('[data-engine-id="local"]')
  const remote = page.locator('[data-engine-id="remote"]')
  await expect(local.getByText('503 tracked records', { exact: true })).toBeVisible()
  await expect(remote.getByText('912 tracked records', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Choose workspaces', exact: true }).click()
  await local.getByRole('checkbox', { name: 'Partnerships', exact: true }).check()
  await remote.getByRole('checkbox', { name: 'Partnerships', exact: true }).check()
  await local.getByRole('checkbox', { name: 'All work', exact: true }).uncheck()
  await expect(local.locator('.overview-workspace')).toHaveCount(1)
  await expect(remote.locator('.overview-workspace')).toHaveCount(2)
  await expect(local.getByText('31 tracked records', { exact: true })).toBeVisible()
  await expect(remote.getByText('68 tracked records', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Choose workspaces', exact: true }).click()
  await page.screenshot({ path: testInfo.outputPath('overview-desktop.png') })
  const stored = await page.evaluate(key => localStorage.getItem(key), WATCH_STORAGE_KEY)
  expect(JSON.parse(stored!)).toEqual({ [watchKey('local', workspaceId)]: true, [watchKey('remote', workspaceId)]: true, [watchKey('local', 'all')]: false })
  const browserStorage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(browserStorage).not.toContain(remoteToken)
  expect(browserStorage).not.toContain('912')
  await remote.locator(`[data-workspace-id="${workspaceId}"]`).getByRole('button', { name: 'Open workspace', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Review queue', exact: true })).toBeVisible()
  await expect(page.getByLabel('Engine', { exact: true })).toHaveValue('remote')
  await expect(page.getByLabel('Workspace', { exact: true })).toHaveValue(workspaceId)
  expect(requests.filter(request => request.method !== 'GET' && request.method !== 'OPTIONS')).toEqual([])
  expect(requests.filter(request => request.engine === 'local').every(request => request.authorization === undefined)).toBe(true)
  expect(requests.filter(request => request.engine === 'remote' && request.method !== 'OPTIONS' && request.path !== '/v1/setup/bootstrap').every(request => request.authorization === `Bearer ${remoteToken}`)).toBe(true)
  expect(requests.filter(request => request.path === '/v1/setup/bootstrap').every(request => request.authorization === undefined)).toBe(true)
  expect(consoleErrors).toEqual([])
  await page.reload()
  await navigate(page, 'Overview')
  await expect(local.locator('.overview-workspace')).toHaveCount(1)
  await expect(local.getByRole('heading', { name: 'Partnerships', exact: true })).toBeVisible()
  await expect(remote.getByText('Access required', { exact: true })).toBeVisible()
  await expect(remote.locator('.overview-workspace')).toHaveCount(0)
})

test('retains a clearly stale snapshot when one engine fails and refreshes only through reads', async ({ page }, testInfo) => {
  const { state, requests } = await environment(page)
  await page.goto('/')
  await unlockRemote(page)
  const local = page.locator('[data-engine-id="local"]')
  const remote = page.locator('[data-engine-id="remote"]')
  state.remoteStatus = 503
  state.localReady = 281
  await page.getByRole('button', { name: 'Refresh overview', exact: true }).click()
  await expect(remote.getByText('STALE SNAPSHOT', { exact: true })).toBeVisible()
  await expect(remote.getByText('912 tracked records', { exact: true })).toBeVisible()
  await expect(remote.getByText(/Last successful check/)).toBeVisible()
  await expect(remote.getByRole('button', { name: 'Open workspace', exact: true })).toBeDisabled()
  await expect(local.locator('.overview-metrics dd').first()).toHaveText('281')
  await expect(local.getByText('Connected', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('overview-stale-desktop.png') })
  state.remoteStatus = 401
  await page.getByRole('button', { name: 'Refresh overview', exact: true }).click()
  await expect(remote.getByText('Access required', { exact: true })).toBeVisible()
  await expect(remote.getByRole('button', { name: 'Unlock engine', exact: false })).toBeVisible()
  state.remoteStatus = 200
  await page.getByRole('button', { name: 'Refresh overview', exact: true }).click()
  await expect(remote.getByText('Connected', { exact: true })).toBeVisible()
  await expect(remote.getByText('STALE SNAPSHOT', { exact: true })).toHaveCount(0)
  expect(requests.filter(request => request.method !== 'GET' && request.method !== 'OPTIONS')).toEqual([])
})

test('refresh can be paused, and mobile workspace controls fit without overflow', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.clock.install()
  const { requests } = await environment(page)
  await page.goto('/')
  await unlockRemote(page)
  await page.getByRole('checkbox', { name: 'Refresh every 30 seconds', exact: true }).uncheck()
  const count = () => requests.filter(request => request.path === '/v1/ui/overview').length
  const pausedCount = count()
  await page.clock.runFor(31_000)
  expect(count()).toBe(pausedCount)
  await page.getByRole('checkbox', { name: 'Refresh every 30 seconds', exact: true }).check()
  await page.clock.runFor(30_000)
  await expect.poll(count).toBeGreaterThan(pausedCount)
  await page.getByRole('button', { name: 'Choose workspaces', exact: true }).click()
  const local = page.locator('[data-engine-id="local"]')
  await local.getByRole('checkbox', { name: 'Partnerships', exact: true }).check()
  await expect(local.locator('.overview-workspace')).toHaveCount(2)
  await page.getByRole('button', { name: 'Choose workspaces', exact: true }).click()
  await page.locator('.overview-view').evaluate(element => { element.scrollTop = 0 })
  await page.screenshot({ path: testInfo.outputPath('overview-mobile.png') })
  const width = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }))
  expect(width.content).toBeLessThanOrEqual(width.viewport)
  await local.locator(`[data-workspace-id="${workspaceId}"]`).getByRole('button', { name: 'Stop watching Partnerships in This engine', exact: true }).click()
  await expect(local.locator('.overview-workspace')).toHaveCount(1)
  await page.locator('[data-engine-id="remote"]').getByRole('button', { name: 'Open workspace', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Review queue', exact: true })).toBeVisible()
})

test('cancels a pending overview when leaving and clears the locked engine snapshot', async ({ page }) => {
  await environment(page)
  await page.goto('/')
  await unlockRemote(page)
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  await page.route(`${remoteUrl}/v1/ui/overview`, async route => {
    await pending
    // The component cancels this request when it unmounts. A late server answer is discarded.
    try { await route.fulfill({ json: summary(true), headers: { 'Access-Control-Allow-Origin': '*' } }) }
    catch { /* The request was already cancelled by the browser. */ }
  })
  try {
    const started = page.waitForRequest(`${remoteUrl}/v1/ui/overview`)
    await page.getByRole('button', { name: 'Refresh overview', exact: true }).click()
    await started
    const cancelled = page.waitForEvent('requestfailed', request => request.url() === `${remoteUrl}/v1/ui/overview`)
    await navigate(page, 'Connections')
    const request = await cancelled
    expect(request.failure()?.errorText).toContain('ABORTED')
    const remoteCard = page.locator('.connection-card').filter({ has: page.getByRole('heading', { name: 'Studio engine', exact: true }) })
    await remoteCard.getByRole('button', { name: 'Lock', exact: true }).click()
    await expect(remoteCard.getByText('Locked', { exact: true })).toBeVisible()
    release()
    await navigate(page, 'Overview')
    const remote = page.locator('[data-engine-id="remote"]')
    await expect(remote.getByText('Access required', { exact: true })).toBeVisible()
    await expect(remote.locator('.overview-workspace')).toHaveCount(0)
    await expect(page.locator('[data-engine-id="local"]').getByText('503 tracked records', { exact: true })).toBeVisible()
  } finally { release() }
})
