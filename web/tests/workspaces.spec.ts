import { expect, test } from '@playwright/test'
import { connect, mockEngine, navigate } from './mock-engine'
import type { Workspace } from '../src/data/workspaces'
import { createFixtures } from '../src/data/fixtures'

test('creates, edits, switches and removes a custom workspace without changing records', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  await mockEngine(page)
  let items: Workspace[] = []
  const writes: string[] = []
  const loadedNames: string[] = []
  await page.route('**/v1/ui/records?*', async route => {
    const filtered = new URL(route.request().url()).searchParams.has('workspace_id')
    const name = items[0]?.kinds.includes('grant_application') ? 'Grant coordinator' : 'Research contact'
    if (filtered) loadedNames.push(name)
    const record = { ...createFixtures()[0], kind: name === 'Grant coordinator' ? 'grant_application' : 'research_partner', contact: { ...createFixtures()[0].contact!, name } }
    return route.fulfill({ json: { items: [record], total: 1, has_more: false, as_of: new Date().toISOString() } })
  })
  await page.route('**/v1/ui/workspaces**', async route => {
    const method = route.request().method()
    if (method === 'GET') return route.fulfill({ json: { items, available_kinds: ['partnership', 'grant_application'], scope: 'shared_engine' } })
    writes.push(method)
    if (method === 'DELETE') { items = []; return route.fulfill({ status: 204 }) }
    const item = { ...route.request().postDataJSON(), id: '88f1f48e-c432-4336-a28a-7828c2545a20', created_at: '', updated_at: '' }
    items = [item]
    return route.fulfill({ status: method === 'POST' ? 201 : 200, json: item })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Setup', exact: true })).toBeVisible()
  await expect(page.getByText(/fictional records|Demo workspace/)).toHaveCount(0)
  await connect(page)
  await page.getByRole('button', { name: 'Manage workspaces' }).click()
  await page.getByLabel('Workspace name', { exact: true }).fill('Research partners')
  await page.getByLabel('Description', { exact: false }).fill('Conversations with research organizations.')
  await page.getByLabel('Add a record type').fill('research_partner')
  await page.getByRole('button', { name: 'Add type', exact: true }).click()
  await page.getByRole('button', { name: 'Save workspace', exact: true }).click()
  await expect(page.getByText('Workspace saved to your engine.', { exact: true })).toBeVisible()
  expect(items[0]).toMatchObject({ name: 'Research partners', kinds: ['research_partner'] })
  await expect(page.getByLabel('Workspace', { exact: true })).toHaveValue(items[0].id)
  await page.setViewportSize({ width: 1251, height: 992 })
  await page.screenshot({ path: testInfo.outputPath('desktop-workspace.png') })
  await page.getByLabel('Workspace name', { exact: true }).fill('Research & grants')
  await page.getByRole('checkbox', { name: 'grant_application', exact: true }).check()
  await page.getByRole('button', { name: 'Save workspace', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Research & grants', exact: true })).toBeVisible()
  expect(items[0].kinds).toEqual(['research_partner', 'grant_application'])
  await expect.poll(() => loadedNames.includes('Grant coordinator')).toBe(true)
  await page.getByRole('button', { name: 'Open workspace →', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Review queue', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Selected record' }).getByRole('heading', { name: 'Grant coordinator', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Manage workspaces' }).click()
  await page.getByRole('button').filter({ has: page.getByRole('heading', { name: 'Research & grants', exact: true }) }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: testInfo.outputPath('mobile-workspace.png') })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: 'Remove workspace', exact: true }).click()
  await page.getByRole('button', { name: 'Confirm removal', exact: true }).click()
  await expect(page.getByText('Workspace removed. Your records are unchanged.', { exact: true })).toBeVisible()
  await navigate(page, 'Review queue')
  await expect(page.locator('.record-row').first()).toBeVisible()
  expect(writes).toEqual(['POST', 'PUT', 'DELETE'])
  expect(errors).toEqual([])
})
