import { expect, test, type Page } from '@playwright/test'
import type { ModelStatus, SetupStatus } from '../src/data/setup'

const access = 'setup-test-review-access'
const initialModel: ModelStatus = { backend: 'openai_compatible', source: 'environment', base_url: 'http://litellm:4000', model_alias: 'respawned-default', timeout_seconds: 60, api_key_env: 'LITELLM_MASTER_KEY', key_configured: false, ready: false, verified: false, error: null }

async function environment(page: Page, databaseReady = true, outbox: SetupStatus['outbox'] = { mode: 'export_only', automatic_delivery: false, export_url: '/v1/ui/outbox/export' }) {
  let model = structuredClone(initialModel)
  const writes: { path: string; body: unknown }[] = []
  const unexpected: string[] = []
  await page.route('**/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/v1/setup/bootstrap') return route.fulfill({ json: { review_enabled: true } })
    if (path === '/v1/ui/session' && method === 'GET') return route.fulfill({ json: { authenticated: false, local_launcher: false } })
    if (request.headers().authorization !== `Bearer ${access}`) return route.fulfill({ status: 401, json: { detail: 'Review access token was not accepted' } })
    if (method !== 'GET') writes.push({ path, body: request.postDataJSON() })
    if (path === '/v1/ui/config') return route.fulfill({ json: { policy_mode: 'human', cooldown_hours: 48, max_draft_characters: 320, source_freshness: 'unknown' } })
    if (path === '/v1/ui/setup') {
      const body: SetupStatus = {
        database: { status: databaseReady ? 'ready' : 'unavailable', message: databaseReady ? 'PostgreSQL is available' : 'Check the server DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD settings' },
        review: { enabled: true, authentication: 'bearer', token_env: 'RESPAWNED_REVIEW_TOKEN' },
        model,
        outbox,
        sources: { mode: 'api_import', import_url: '/v1/ui/import' },
      }
      return route.fulfill({ json: body })
    }
    if (path === '/v1/ui/setup/model' && method === 'PUT') {
      const payload = request.postDataJSON()
      model = { ...model, ...payload, source: 'saved', ready: true,
        key_configured: payload.backend !== 'codex_cli' }
      return route.fulfill({ json: model })
    }
    if (path === '/v1/ui/import' && method === 'POST') {
      const payload = request.postDataJSON()
      return route.fulfill({ json: { opportunities_upserted: payload.opportunities.length, activities_inserted: payload.activities.length } })
    }
    if (path === '/v1/ui/workspaces') return route.fulfill({ json: { items: [], available_kinds: [], scope: 'shared_engine' } })
    if (path === '/v1/ui/records') return route.fulfill({ json: { items: [], total: 0, has_more: false, as_of: '2026-09-09T18:00:00Z' } })
    if (path === '/v1/ui/outbox') return route.fulfill({ json: { items: [], has_more: false } })
    if (path === '/v1/ui/inbox') return route.fulfill({ json: { items: [], total: 0, has_more: false, as_of: '2026-09-09T18:00:00Z', source_freshness: 'unknown', outreach_eligibility: 'not_evaluated' } })
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'Unexpected setup request' } })
  })
  return { writes, unexpected, setOutbox: (next: SetupStatus['outbox']) => { outbox = next } }
}

async function connect(page: Page) {
  await page.getByLabel('Review access token', { exact: true }).fill(access)
  await page.getByRole('button', { name: 'Connect local engine', exact: true }).click()
  await expect(page.getByText('Unlocked', { exact: true })).toBeVisible()
  await expect(page.getByLabel('API base URL')).toBeVisible()
}

test('starts empty with server setup, and review access clears on reload', async ({ page }, testInfo) => {
  const { writes, unexpected } = await environment(page, false)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Setup', exact: true })).toBeVisible()
  await expect(page.locator('.record-row')).toHaveCount(0)
  await expect(page.getByText(/fictional|demo workspace/i)).toHaveCount(0)
  await page.getByText('Using a remote server or API client?', { exact: true }).click()
  await expect(page.getByText(/Respawned manages that version check automatically/)).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('setup-access-desktop.png') })
  await connect(page)
  await expect(page.getByText('Database unavailable', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save model settings', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Import records', exact: true })).toBeDisabled()
  expect(writes).toEqual([])
  const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(stored).not.toContain(access)
  await page.reload()
  await expect(page.getByLabel('Review access token', { exact: true })).toHaveValue('')
  await expect(page.getByText('Locked', { exact: true })).toBeVisible()
  expect(unexpected).toEqual([])
})

test('saves model configuration without inference and imports only after explicit submission', async ({ page }, testInfo) => {
  const { writes, unexpected } = await environment(page)
  await page.goto('/')
  await connect(page)
  await page.getByLabel('API base URL').fill('http://localhost:11434/v1')
  await page.getByLabel('Model or proxy alias').fill('local-writing-model')
  await page.getByLabel('Timeout (seconds)').fill('45')
  await page.getByLabel('Server credential variable').selectOption('RESPAWNED_MODEL_API_KEY')
  expect(writes).toHaveLength(0)
  await page.getByRole('button', { name: 'Save model settings', exact: true }).click()
  await expect(page.getByText('Model settings saved. No model request was made.', { exact: true })).toBeVisible()
  expect(writes).toEqual([{ path: '/v1/ui/setup/model', body: { backend: 'openai_compatible', base_url: 'http://localhost:11434/v1', model_alias: 'local-writing-model', timeout_seconds: 45, api_key_env: 'RESPAWNED_MODEL_API_KEY' } }])
  await page.screenshot({ path: testInfo.outputPath('setup-model-desktop.png') })
  const payload = { opportunities: [{ id: 'source-42', kind: 'partnership', title: 'Workshop planning', status: 'open', created_at: '2026-09-01T09:00:00Z' }], activities: [] }
  await page.getByLabel('Or paste your import JSON').fill(JSON.stringify(payload))
  await expect(page.getByText('Ready to validate: 1 record · 0 activities', { exact: true })).toBeVisible()
  expect(writes).toHaveLength(1)
  await expect(page.getByText(/Matching record IDs replace the full saved snapshot/)).toBeVisible()
  await page.getByRole('button', { name: 'Import records', exact: true }).click()
  await expect(page.getByText(/Imported 1 record and 0 new activities/)).toBeVisible()
  expect(writes[1]).toEqual({ path: '/v1/ui/import', body: payload })
  expect(writes).toHaveLength(2)
  await page.getByRole('button', { name: 'Open outbox', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Nothing in the outbox yet', exact: true })).toBeVisible()
  expect(unexpected).toEqual([])
})

test('mobile setup keeps configuration and import controls reachable without overflow', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const { unexpected } = await environment(page)
  await page.goto('/')
  await connect(page)
  await page.getByLabel('API base URL').scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('setup-model-mobile.png') })
  await page.getByLabel('Or paste your import JSON').scrollIntoViewIfNeeded()
  await page.getByText('Import format & API integration', { exact: true }).click()
  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download template', exact: true }).click()
  expect((await downloading).suggestedFilename()).toBe('respawned-import-template.json')
  await page.getByRole('button', { name: 'Import records', exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('setup-import-mobile.png') })
  const dimensions = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }))
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.width)
  await expect(page.getByRole('button', { name: 'Import records', exact: true })).toBeDisabled()
  expect(unexpected).toEqual([])
})

test('saves optional CLI adapter settings without claiming runtime or credential verification', async ({ page }) => {
  const { writes, unexpected } = await environment(page)
  await page.goto('/')
  await connect(page)
  const modelSection = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Model backend', exact: true }) })
  await expect(modelSection.getByText('Used to generate drafts.', { exact: true })).toBeVisible()
  await expect(modelSection.getByText('RESPAWNED_CODEX_BIN', { exact: true })).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Backend', exact: true }).selectOption('codex_cli')
  await expect(page.getByLabel('API base URL')).toHaveCount(0)
  await expect(page.getByLabel('Server credential variable')).toHaveCount(0)
  await expect(page.getByLabel('Model override (optional)')).toHaveValue('')
  await expect(page.getByLabel('Model override (optional)')).not.toHaveAttribute('required')
  await expect(page.getByLabel('Timeout (seconds)')).toHaveValue('120')
  await expect(modelSection.getByText('Configured means the required settings are present. Connection and inference are unverified.', { exact: true })).toBeVisible()
  await expect(modelSection.getByText(/login|Save to check the CLI/i)).toHaveCount(0)
  expect(writes).toEqual([])

  await page.getByRole('button', { name: 'Save model settings', exact: true }).click()
  await expect(page.getByText('Model settings saved. No model request was made.', { exact: true })).toBeVisible()
  const expectedWrite = { path: '/v1/ui/setup/model', body: {
    backend: 'codex_cli', base_url: '', model_alias: '', timeout_seconds: 120,
    api_key_env: 'LITELLM_MASTER_KEY',
  } }
  expect(writes).toEqual([expectedWrite])
  await expect(modelSection.getByText('Configured', { exact: true })).toBeVisible()
  await expect(modelSection.getByText('Configured means the required settings are present. Connection and inference are unverified.', { exact: true })).toBeVisible()
  await expect(modelSection.getByText(/Using saved model settings/)).toBeVisible()

  await page.getByRole('button', { name: 'Refresh status', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Backend', exact: true })).toHaveValue('codex_cli')
  await expect(modelSection.getByText('Configured', { exact: true })).toBeVisible()
  await expect(page.getByLabel('API base URL')).toHaveCount(0)
  expect(writes).toEqual([expectedWrite])
  expect(unexpected).toEqual([])
})

test('shows the engine outbox capability without collecting credentials or performing delivery', async ({ page }, testInfo) => {
  const outbox: SetupStatus['outbox'] = {
    mode: 'api_and_export', automatic_delivery: false, export_url: '/v1/ui/outbox/export',
    pending_url: '/v1/outbox/pending', receipt_url: '/v1/outbox/{id}/receipt',
    token_env: 'RESPAWNED_OUTBOX_TOKEN', token_configured: false,
  }
  const { writes, unexpected, setOutbox } = await environment(page, true, outbox)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', entry => { if (entry.type() === 'error') errors.push(entry.text()) })
  await page.goto('/')
  const section = page.getByRole('region', { name: 'Outbox & delivery', exact: true })
  await expect(section.getByText('Export only', { exact: true })).toHaveCount(0)
  await expect(section.getByRole('link', { name: 'Outbox API documentation', exact: true })).toHaveCount(0)
  await connect(page)
  await expect(section.getByText('GET /v1/outbox/pending', { exact: true })).toBeVisible()
  await expect(section.getByText('POST /v1/outbox/{id}/receipt', { exact: true })).toBeVisible()
  await expect(section.getByText(/The outbox token is not configured on this server/)).toBeVisible()
  await expect(section.locator('input, textarea')).toHaveCount(0)
  await expect(section.getByRole('link', { name: 'Outbox API documentation', exact: true })).toHaveAttribute('href', 'https://respawned.williamshayden.com/api/#outbox-integration')
  await section.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('outbox-integration-desktop.png') })
  await page.setViewportSize({ width: 390, height: 844 })
  await section.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('outbox-integration-mobile.png') })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)

  setOutbox({ ...outbox, token_configured: true })
  await page.getByRole('button', { name: 'Refresh status', exact: true }).click()
  await expect(section.getByText(/The outbox token is configured on this server/)).toBeVisible()
  setOutbox({ mode: 'export_only', automatic_delivery: false, export_url: '/v1/ui/outbox/export' })
  await page.getByRole('button', { name: 'Refresh status', exact: true }).click()
  await expect(section.getByText('Export only', { exact: true })).toBeVisible()
  await expect(section.getByText(/GET \/v1\/outbox\/pending|POST \/v1\/outbox/)).toHaveCount(0)
  await expect(section.getByRole('link', { name: 'Outbox API documentation', exact: true })).toHaveCount(0)
  await section.getByRole('button', { name: 'Open outbox', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Nothing in the outbox yet', exact: true })).toBeVisible()
  expect(writes).toEqual([])
  expect(unexpected).toEqual([])
  expect(errors).toEqual([])
})
