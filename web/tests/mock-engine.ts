import { expect, type Page } from '@playwright/test'
import { createDemoClient } from '../src/data/demoClient'
import { createFixtures } from '../src/data/fixtures'
import { SESSION_PROOF_STORAGE_KEY } from '../src/data/auth'

// Sample data belongs in test fixtures, never in the product's startup path.
export async function mockEngine(page: Page, options: { canonical?: boolean; localSession?: boolean; empty?: boolean } = {}) {
  const client = createDemoClient(null)
  const prefix = options.canonical ? '/v1/workflow' : '/v1/ui'
  const importedIds = options.empty ? new Set<string>() : null
  const writes: { path: string; body: unknown }[] = []
  if (options.localSession) await page.addInitScript(key => localStorage.setItem(key, 'mock-local-origin-proof'), SESSION_PROOF_STORAGE_KEY)
  await page.route('**/v1/setup/bootstrap', route => route.fulfill({ json: { review_enabled: true, ...(options.canonical ? { workflow_api_prefix: prefix } : {}) } }))
  await page.route(`**${prefix}/**`, async route => {
    const url = new URL(route.request().url())
    const path = url.pathname.slice(prefix.length)
    const payload = route.request().postDataJSON()
    if (route.request().method() !== 'GET') writes.push({ path, body: payload })
    const kind = url.searchParams.get('workspace_id')
    try {
      let json: unknown
      if (path === '/session') json = { authenticated: options.localSession ?? false, local_launcher: options.localSession ?? false }
      else if (path === '/config') json = await client.config()
      else if (path === '/workspaces') json = { items: options.empty ? [] : [...new Set(createFixtures().map(record => record.kind))].map(kind => ({ id: kind, name: kind, description: '', kinds: [kind], created_at: '', updated_at: '' })), available_kinds: [], scope: 'shared_engine' }
      else if (path === '/setup') json = {
        database: { status: 'ready', message: 'Test database ready' }, review: { authorized: true, token_storage: 'memory' },
        model: { base_url: 'http://127.0.0.1:4000', model_alias: 'respawned-default', api_key_env: 'LITELLM_MASTER_KEY', timeout_seconds: 60, source: 'environment', key_configured: false, ready: false },
        outbox: { mode: 'export_only', delivery_enabled: false }, sources: { mode: 'import', freshness: 'unknown' },
      }
      else if (path === '/records') {
        const records = (await client.listRecords()).items.filter(record => (!importedIds || importedIds.has(record.id)) && (!kind || record.kind === kind))
        const offset = Number(url.searchParams.get('offset') ?? 0)
        json = { items: records.slice(offset, offset + 50), total: records.length, has_more: records.length > offset + 50, as_of: new Date().toISOString() }
      } else if (path === '/import') {
        for (const record of payload.opportunities) importedIds?.add(record.id)
        json = { opportunities_upserted: payload.opportunities.length, activities_inserted: payload.activities.length }
      } else if (path === '/inbox') {
        const inbox = await client.listInbox()
        const items = inbox.items.filter(item => !importedIds || item.opportunity_ids.some(id => importedIds.has(id)))
        json = { ...inbox, items, total: items.length }
      }
      else if (path === '/outbox') json = { items: await client.listOutbox(), has_more: false }
      else if (path === '/sync') json = await client.sync()
      else if (path.startsWith('/records/') && path.endsWith('/draft')) json = await client.draft(decodeURIComponent(path.slice(9, -6)), payload?.body)
      else if (path.startsWith('/records/')) json = await client.getRecord(decodeURIComponent(path.slice(9)))
      else if (path.startsWith('/drafts/')) {
        const [, , id, action] = path.split('/')
        if (action === 'edit') json = await client.edit(decodeURIComponent(id), payload.body, payload.review_token)
        else if (action === 'approve') json = await client.approve(decodeURIComponent(id), payload.review_token)
        else json = await client.reject(decodeURIComponent(id), payload.review_token)
      } else throw new Error(`Unexpected route ${path}`)
      await route.fulfill({ json })
    } catch (error) { await route.fulfill({ status: 409, json: { detail: String(error) } }) }
  })
  return { client, writes }
}

export async function navigate(page: Page, name: string) {
  const menu = page.getByRole('button', { name: 'Open navigation', exact: true })
  if (await menu.isVisible() && !await page.getByRole('navigation', { name: 'Main navigation' }).isVisible()) await menu.click()
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: new RegExp(`^${name}`) }).click()
}

export async function connect(page: Page, scope = 'all') {
  await page.getByLabel('Engine access token', { exact: true }).fill('browser-test-operator-token')
  await page.getByRole('button', { name: 'Connect local engine', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Engine connected', exact: true, includeHidden: true })).toBeAttached()
  await navigate(page, 'Review queue')
  if (scope !== 'all') await setWorkspace(page, scope)
}

export async function setWorkspace(page: Page, scope: string) {
  const menu = page.getByRole('button', { name: 'Open navigation', exact: true })
  if (await menu.isVisible() && !await page.getByRole('navigation', { name: 'Main navigation' }).isVisible()) await menu.click()
  await page.getByLabel('Workspace', { exact: true }).selectOption(scope)
  if (await menu.isVisible()) await page.getByRole('button', { name: 'Close navigation', exact: true }).last().click()
}
