import { expect, test, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { navigate, setWorkspace } from './mock-engine'

const fixturePath = fileURLToPath(new URL('./fixtures/ui-import.json', import.meta.url))
const token = 'ui-simulation-review-token'

async function unlock(page: Page) {
  await page.getByLabel('Engine access token', { exact: true }).fill(token)
  await page.getByRole('button', { name: 'Connect local engine', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Engine connected', exact: true, includeHidden: true })).toBeAttached()
}

// All application state is written and checked through browser controls. There
// are no mocked routes, direct API calls, SQL assertions, or injected app state.
test('UI-only import, workspace, review, stale edit, and export on an empty engine', async ({ page, context }, testInfo) => {
  test.skip(process.env.RESPAWNED_LIVE_UI_TEST !== '1', 'Start serve_ui_simulation.py --empty and set RESPAWNED_LIVE_UI_URL.')
  test.setTimeout(120_000)
  const url = new URL(process.env.RESPAWNED_LIVE_UI_URL || '')
  expect(['127.0.0.1', 'localhost', '[::1]']).toContain(url.hostname)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await unlock(page)
  await expect(page.getByLabel('Or paste your import JSON', { exact: true })).toBeEnabled()

  await test.step('start empty and reject an invalid import without partial records', async () => {
    await navigate(page, 'Overview')
    await expect(page.getByText('0 tracked records', { exact: true })).toBeVisible()
    await navigate(page, 'Setup')
    const fixture = JSON.parse(await readFile(fixturePath, 'utf8'))
    fixture.opportunities[0].created_at = 'missing-timezone'
    await page.getByLabel('Or paste your import JSON', { exact: true }).fill(JSON.stringify(fixture))
    await page.getByRole('button', { name: 'Import records', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('created_at')
    await navigate(page, 'Overview')
    await expect(page.getByText('0 tracked records', { exact: true })).toBeVisible()
  })

  await test.step('import canonical JSON through the file chooser and replay safely', async () => {
    await navigate(page, 'Setup')
    await page.getByLabel('Choose a JSON file', { exact: true }).setInputFiles(fixturePath)
    await page.getByRole('button', { name: 'Import records', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('Imported 3 records and 4 new activities')
    await page.getByLabel('Choose a JSON file', { exact: true }).setInputFiles(fixturePath)
    await page.getByRole('button', { name: 'Import records', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('Imported 3 records and 0 new activities')
  })

  let workspaceId = ''
  await test.step('save model configuration without an inference call', async () => {
    await page.getByLabel('API base URL').fill('http://127.0.0.1:11434/v1')
    await page.getByLabel('Model or proxy alias', { exact: true }).fill('deterministic-ui-stub')
    await page.getByRole('button', { name: 'Save model settings', exact: true }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Model settings saved' })).toBeVisible()
  })

  await test.step('create and watch an application workspace', async () => {
    await page.getByRole('button', { name: 'Manage workspaces', exact: true }).click()
    await page.getByLabel('Workspace name', { exact: true }).fill('Applications')
    await page.getByRole('checkbox', { name: 'job_application', exact: true }).check()
    await page.getByRole('button', { name: 'Save workspace', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('Workspace saved')
    workspaceId = await page.getByLabel('Workspace', { exact: true }).inputValue()
    await page.getByLabel(/Description/).fill('Applications awaiting replies')
    await page.getByRole('button', { name: 'Save workspace', exact: true }).click()
    await expect(page.locator('.workspace-card').getByText('Applications awaiting replies', { exact: true })).toBeVisible()
    await navigate(page, 'Overview')
    await page.getByRole('button', { name: 'Choose workspaces', exact: true }).click()
    await page.getByRole('checkbox', { name: 'Applications', exact: true }).check()
    await expect(page.getByRole('heading', { name: 'Applications', exact: true })).toBeVisible()
  })

  const reviewed = 'Hi Maya, thanks again for discussing the Backend Engineer role. Is there an update on next steps? Best, Jordan'
  await test.step('draft and edit the contactable application', async () => {
    await navigate(page, 'Review queue')
    await setWorkspace(page, workspaceId)
    await page.getByRole('button', { name: /Maya Chen/ }).click()
    await page.getByRole('button', { name: 'Generate draft', exact: true }).click()
    await page.getByRole('textbox', { name: 'Draft message', exact: true }).fill(reviewed)
    await page.getByRole('button', { name: 'Save changes', exact: true }).click()
    await expect(page.getByText('Draft changes saved.', { exact: true })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('review-desktop.png') })
  })

  await test.step('a second browser tab cannot save a stale draft', async () => {
    const stale = await context.newPage()
    stale.on('pageerror', error => errors.push(error.message))
    await stale.goto('/')
    await unlock(stale)
    await navigate(stale, 'Review queue')
    await setWorkspace(stale, workspaceId)
    await stale.getByRole('button', { name: /Maya Chen/ }).click()
    await stale.getByRole('textbox', { name: 'Draft message', exact: true }).fill('Hi Maya, this older tab still has an outdated draft. Best, Jordan')
    await page.getByRole('button', { name: 'Approve to outbox', exact: true }).click()
    await expect(page.getByText('Added to outbox. Your message is unsent.', { exact: true })).toBeVisible()
    await stale.getByRole('button', { name: 'Save changes', exact: true }).click()
    await expect(stale.getByRole('alert')).toContainText(/changed|pending|stale|approved/i)
    await stale.close()
  })

  await test.step('export the approved snapshot and keep it unsent after reload', async () => {
    await navigate(page, 'Outbox')
    await expect(page.getByText(reviewed, { exact: true })).toBeVisible()
    await expect(page.getByText('Unsent', { exact: true })).toHaveCount(1)
    const downloaded = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Export CSV', exact: true }).click()
    const file = await downloaded
    expect(file.suggestedFilename()).toBe('respawned-outbox.csv')
    const filePath = testInfo.outputPath('outbox.csv')
    await file.saveAs(filePath)
    const csv = await readFile(filePath, 'utf8')
    expect(csv).toContain(reviewed)
    expect(csv).toContain('maya@northstar.example')
    await page.reload()
    await unlock(page)
    await expect(page.getByLabel('Model or proxy alias', { exact: true })).toHaveValue('deterministic-ui-stub')
    await navigate(page, 'Outbox')
    await expect(page.getByText(reviewed, { exact: true })).toBeVisible()
    await expect(page.getByText('Unsent', { exact: true })).toHaveCount(1)
    await page.screenshot({ path: testInfo.outputPath('outbox-desktop.png') })
  })

  await test.step('contactless records remain inspectable and cannot draft', async () => {
    await navigate(page, 'Review queue')
    await setWorkspace(page, workspaceId)
    await page.getByRole('button', { name: 'All tracked', exact: true }).click()
    await page.getByRole('button', { name: /Platform Engineer/ }).click()
    await expect(page.getByRole('button', { name: 'Generate draft', exact: true })).toHaveCount(0)
    await expect(page.getByText('Human contact not identified', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Approve to outbox', exact: true })).toBeDisabled()
    await page.setViewportSize({ width: 390, height: 844 })
    await navigate(page, 'Overview')
    await expect(page.getByRole('heading', { name: 'Applications', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('overview-mobile.png'), fullPage: true })
  })

  await test.step('remove the saved view while retaining approved records', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.getByRole('button', { name: 'Manage workspaces', exact: true }).click()
    await page.locator('.workspace-card').getByRole('button').filter({ hasText: 'Applications' }).click()
    await page.getByRole('button', { name: 'Remove workspace', exact: true }).click()
    await page.getByRole('button', { name: 'Confirm removal', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('Workspace removed')
    await navigate(page, 'Overview')
    await expect(page.getByText('3 tracked records', { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Applications', exact: true })).toHaveCount(0)
    await navigate(page, 'Outbox')
    await expect(page.getByText(reviewed, { exact: true })).toBeVisible()
    await expect(page.getByText('Unsent', { exact: true })).toHaveCount(1)
  })
  expect(errors).toEqual([])
})
