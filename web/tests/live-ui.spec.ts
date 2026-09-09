import { expect, test } from '@playwright/test'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

test('connected review persists an edited draft in the PostgreSQL outbox', async ({ page, request }) => {
  test.skip(process.env.RESPAWNED_LIVE_UI_TEST !== '1', 'Requires the explicitly started, isolated serve_ui_simulation.py harness.')
  const token = 'ui-simulation-review-token'
  const headers = { Authorization: `Bearer ${token}` }
  const records = await request.get('/v1/ui/records', { headers })
  expect(records.ok()).toBeTruthy()
  const fixture = (await records.json()).items.find((record: { id: string }) => record.id === 'northstar-backend')
  expect(fixture?.fields).toContainEqual({ label: 'Company', value: 'Northstar' })

  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 1586, height: 992 })
  await page.goto('/')
  await page.getByLabel('Review access token').fill(token)
  await page.getByRole('button', { name: 'Connect local engine' }).click()
  await expect(page.getByRole('button', { name: 'Engine connected', exact: true })).toBeVisible()
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: /^Review queue/ }).click()
  await page.getByRole('button', { name: /Maya Chen/ }).click()
  await page.getByRole('button', { name: 'Generate draft', exact: true }).click()
  const draft = page.getByRole('textbox', { name: 'Draft message', exact: true })
  await expect(draft).toBeVisible()
  const message = 'Hi Maya, thanks again for discussing the Backend Engineer role. Is there an update on next steps? Best, William'
  await draft.fill(message)
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByText('Draft changes saved.', { exact: true })).toBeVisible()
  await page.screenshot({ path: join(tmpdir(), 'respawned-connected-review.png') })
  await page.getByRole('button', { name: 'Approve to outbox', exact: true }).click()
  await expect(page.getByText('Added to outbox. Your message is unsent.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /^Outbox/ }).click()
  await expect(page.getByText(message, { exact: true })).toBeVisible()
  await expect(page.getByText('Unsent', { exact: true })).toBeVisible()
  await page.screenshot({ path: join(tmpdir(), 'respawned-connected-outbox.png') })

  const persisted = await request.get('/v1/ui/outbox', { headers })
  expect(persisted.ok()).toBeTruthy()
  const items = (await persisted.json()).items
  expect(items).toHaveLength(1)
  expect(items[0]).toMatchObject({ body: message, status: 'pending', authorization_mode: 'human', sent_at: null })
  const after = await request.get('/v1/ui/records', { headers })
  const tracked = (await after.json()).items.find((record: { id: string }) => record.id === 'northstar-platform')
  expect(tracked.contact).toBeNull()
  expect(tracked.candidate_id).toBeNull()
  expect(tracked.draft).toBeNull()
  expect(errors).toEqual([])
})
