import { expect, test, type Page } from '@playwright/test'
import { createFixtures, DEMO_AS_OF } from '../src/data/fixtures'
import { validateDraft } from '../src/data/client'
import type { InboxItem, OutboxItem, UIRecord } from '../src/data/types'

test.describe.configure({ mode: 'serial' })

interface MockWorkspace {
  records: UIRecord[]
  details?: UIRecord[]
  inbox?: InboxItem[]
  outbox?: OutboxItem[]
  total?: number
  hasMore?: boolean
  nextPage?: UIRecord[]
  approvalGate?: Promise<void>
}

interface RequestLog { method: string; path: string; query: string; body: unknown }

function sampleRecord(id: string, name: string, overrides: Partial<UIRecord> = {}): UIRecord {
  return {
    ...createFixtures()[0], id, title: `Role for ${name}`,
    contact: { key: `contact:${id}`, name, address: `${id.replaceAll('/', '-')}@example.com`, channel: 'email' },
    candidate_id: `candidate:${id}`, draft: null, activities: [], referenced_record_ids: [],
    source_freshness: 'unknown', ...overrides,
  }
}

async function connectMock(page: Page, workspace: MockWorkspace): Promise<RequestLog[]> {
  const requests: RequestLog[] = []
  const allRecords = () => [...workspace.records, ...(workspace.details ?? [])]
  await page.route('**/v1/setup/bootstrap', route => route.fulfill({ json: { review_enabled: true } }))
  await page.route('**/v1/ui/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.slice('/v1/ui'.length)
    const method = request.method()
    const body: unknown = request.postData() ? request.postDataJSON() : null
    requests.push({ method, path, query: url.search, body })
    if (request.headers().authorization !== 'Bearer regression-test-token') {
      await route.fulfill({ status: 401, json: { detail: 'Missing test authorization' } })
      return
    }
    if (path === '/workspaces') {
      await route.fulfill({ json: { items: [{ id: 'sales', name: 'Sales', description: '', kinds: ['sales'], created_at: '', updated_at: '' }], available_kinds: ['sales'], scope: 'shared_engine' } })
    } else if (path === '/setup') {
      await route.fulfill({ json: { database: { ready: true }, review: { authorized: true }, model: { base_url: 'http://127.0.0.1:4000', model_alias: 'test', timeout_seconds: 60, api_key_env: 'LITELLM_MASTER_KEY', source: 'environment', ready: false, key_configured: false }, outbox: { mode: 'export_only', delivery_enabled: false }, sources: { mode: 'import' } } })
    } else if (path === '/config') {
      await route.fulfill({ json: { policy_mode: 'human', cooldown_hours: 48, max_draft_characters: 320, source_freshness: 'unknown' } })
    } else if (path === '/records') {
      const offset = Number(url.searchParams.get('offset') ?? 0)
      await route.fulfill({ json: { items: offset ? workspace.nextPage ?? [] : workspace.records, total: workspace.total ?? workspace.records.length, has_more: !offset && (workspace.hasMore ?? false), as_of: DEMO_AS_OF } })
    } else if (path === '/sync') {
      await route.fulfill({ json: { candidate_count: 1, inserted_count: 0, run_id: 'regression-sync' } })
    } else if (path.startsWith('/records/') && method === 'GET') {
      const record = allRecords().find((item) => item.id === decodeURIComponent(path.slice('/records/'.length)))
      await route.fulfill(record ? { json: record } : { status: 404, json: { detail: 'Record not found' } })
    } else if (path === '/inbox') {
      await route.fulfill({ json: { items: workspace.inbox ?? [], total: workspace.inbox?.length ?? 0, has_more: false, as_of: DEMO_AS_OF, source_freshness: 'unknown', outreach_eligibility: 'not_evaluated' } })
    } else if (path === '/outbox') {
      await route.fulfill({ json: { items: workspace.outbox ?? [], has_more: false } })
    } else if (path.startsWith('/drafts/') && method === 'POST') {
      const [, , encodedDraftId, action] = path.split('/')
      const record = allRecords().find((item) => item.draft?.id === decodeURIComponent(encodedDraftId))
      const payload = body as { body?: string; review_token?: string } | null
      if (!record?.draft || payload?.review_token !== record.draft.review_token) {
        await route.fulfill({ status: 409, json: { detail: 'The displayed draft version changed.' } })
        return
      }
      if (action === 'edit') {
        const errors = validateDraft(payload.body ?? '')
        if (errors.length) {
          await route.fulfill({ status: 422, json: { detail: errors[0] } })
          return
        }
        record.draft.body = payload.body!.trim()
        record.draft.review_token = 'saved-valid-version'
        record.draft.validation_errors = []
        record.next_action = 'follow_up'
        record.reason = { code: 'idle', label: 'Follow-up due', detail: 'The saved draft is ready for human review.' }
      } else if (action === 'reject') {
        record.draft.status = 'rejected'
        record.next_action = 'rejected'
      } else if (action === 'approve') {
        await workspace.approvalGate
        if (record.draft.validation_errors.length || validateDraft(record.draft.body).length) {
          await route.fulfill({ status: 409, json: { detail: 'The persisted draft is not valid.' } })
          return
        }
        record.draft.status = 'approved'
        record.draft.outbox_id = 1
        record.next_action = 'approved'
      } else {
        await route.fulfill({ status: 404, json: { detail: 'Unknown review action' } })
        return
      }
      await route.fulfill({ json: record.draft })
    } else {
      await route.fulfill({ status: 404, json: { detail: `Unexpected test request: ${method} ${path}` } })
    }
  })
  await page.goto('/')
  await page.getByLabel('Review access token', { exact: true }).fill('regression-test-token')
  await page.getByRole('button', { name: 'Connect local engine', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Engine connected', exact: true })).toBeVisible()
  await navigation(page, 'Review queue')
  await expect(page.getByRole('status').filter({ hasText: 'Loading your workspace' })).toHaveCount(0)
  return requests
}

const navigation = (page: Page, name: string) => page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: new RegExp(`^${name}`) }).click()
const panel = (page: Page) => page.getByRole('region', { name: 'Selected record' })

test('scoped refs retain off-page inbox and closed outbox context and open the actual grouped primary', async ({ page }) => {
  const primary = sampleRecord('sales/actual-primary', 'Alex Morgan', {
    kind: 'sales', title: 'Primary renewal', fields: [{ label: 'Opportunity', value: 'Primary renewal' }],
    contact: { key: 'contact:alex', name: 'Alex Morgan', address: 'alex@example.com', channel: 'email' },
    referenced_record_ids: ['sales/reply-evidence'],
  })
  const evidenceRef = { id: 'sales/reply-evidence', kind: 'sales', title: 'Secondary discussion' }
  const requests = await connectMock(page, {
    records: Array.from({ length: 50 }, (_, index) => sampleRecord(`loaded-job-${index}`, `Loaded Job Contact ${index}`)),
    nextPage: [sampleRecord('next-page-job', 'Next Page Contact')],
    details: [primary], total: 125, hasMore: true,
    inbox: [{
      contact_key: 'contact:alex', contact_name: 'Alex Morgan', channel: 'email', contact_address: 'alex@example.com',
      opportunity_ids: [evidenceRef.id], record_refs: [evidenceRef], review_record_id: primary.id,
      latest_reply_at: DEMO_AS_OF, last_outbound_at: null, pending_outbox_count: 0,
      reply_evidence: [{ opportunity_id: evidenceRef.id, activity_id: 'reply-1', occurred_at: DEMO_AS_OF, channel: 'email' }],
    }],
    outbox: [{
      id: 1, draft_id: 'closed-draft', contact_key: 'contact:closed', contact_name: 'Closed Account', contact_address: 'closed@example.com', channel: 'email',
      opportunity_ids: ['sales/closed-account'], record_refs: [{ id: 'sales/closed-account', kind: 'sales', title: 'Closed renewal' }],
      body: 'Previously approved message for a now-closed opportunity.', status: 'pending', authorization_mode: 'human', created_at: DEMO_AS_OF, sent_at: null,
    }],
  })
  await page.getByLabel('Workspace', { exact: true }).selectOption('sales')
  await navigation(page, 'Outbox')
  await expect(page.getByRole('heading', { name: 'Closed Account', exact: true })).toBeVisible()
  await expect(page.getByText('Unsent', { exact: true })).toBeVisible()
  await navigation(page, 'Reply inbox')
  const reply = page.getByRole('button').filter({ hasText: 'Alex Morgan' })
  await expect(reply).toBeVisible()
  await expect(reply).toBeEnabled()
  await reply.click()
  await expect(panel(page).getByRole('heading', { name: 'Alex Morgan', exact: true })).toBeVisible()
  await expect(panel(page).locator('dd').filter({ hasText: /^Primary renewal$/ })).toBeVisible()
  expect(requests.some((request) => request.method === 'GET' && decodeURIComponent(request.path) === '/records/sales/actual-primary')).toBe(true)
  expect(requests.some((request) => request.method === 'GET' && decodeURIComponent(request.path) === '/records/sales/reply-evidence')).toBe(false)
  await page.getByRole('button', { name: 'Refresh queue', exact: true }).click()
  await expect(page.getByText('Queue refreshed.', { exact: true })).toBeVisible()
  await expect(panel(page).locator('dd').filter({ hasText: /^Primary renewal$/ })).toBeVisible()
  await page.getByRole('button', { name: /Load more/ }).click()
  await expect(page.locator('.record-row').filter({ hasText: 'Next Page Contact' })).toBeVisible()
  await expect(panel(page).locator('dd').filter({ hasText: /^Primary renewal$/ })).toBeVisible()
  expect(requests.filter((request) => request.path === '/records').map((request) => request.query)).toContain('?limit=50&offset=50')
  expect(requests.filter((request) => request.path === '/records').map((request) => request.query)).not.toContain('?limit=50&offset=51')
})

function invalidRecord(): UIRecord {
  return sampleRecord('invalid-pending', 'Invalid Draft Contact', {
    next_action: 'blocked', reason: { code: 'invalid_draft', label: 'Draft needs repair', detail: 'This persisted draft exceeds the current length limit.' },
    draft: { id: 'draft-invalid', body: 'x'.repeat(321), status: 'pending', review_token: 'invalid-version', validation_errors: ['Draft exceeds 320 characters.'], outbox_id: null },
  })
}

test('an invalid pending draft remains editable and rejectable without approving invalid copy', async ({ page }) => {
  const record = invalidRecord()
  const requests = await connectMock(page, { records: [record] })
  await page.getByRole('button', { name: 'All tracked', exact: true }).click()
  const draft = page.getByRole('textbox', { name: 'Draft message', exact: true })
  await expect(draft).toBeEditable()
  await expect(page.getByRole('button', { name: 'Approve to outbox', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Reject', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Reject', exact: true }).click()
  await expect(page.getByText('Draft rejected. The record stays in All tracked.', { exact: true })).toBeVisible()
  expect(record.status).toBe('open')
  expect(record.draft?.status).toBe('rejected')
  expect(requests.some((request) => request.path.endsWith('/reject'))).toBe(true)
  expect(requests.some((request) => request.path.endsWith('/approve'))).toBe(false)
})

test('a repaired pending draft cannot be approved until the valid copy is saved', async ({ page }) => {
  const record = invalidRecord()
  const requests = await connectMock(page, { records: [record] })
  await page.getByRole('button', { name: 'All tracked', exact: true }).click()
  const approve = page.getByRole('button', { name: 'Approve to outbox', exact: true })
  await expect(approve).toBeDisabled()
  await page.getByRole('textbox', { name: 'Draft message', exact: true }).fill('Hi, is there an update on the next steps?')
  await expect(approve).toBeDisabled()
  await page.getByRole('button', { name: 'Save changes', exact: true }).click()
  await expect(page.getByText('Draft changes saved.', { exact: true })).toBeVisible()
  await expect(approve).toBeEnabled()
  await approve.click()
  await expect(page.getByText('Added to outbox. Your message is unsent.', { exact: true })).toBeVisible()
  expect(requests.find((request) => request.path.endsWith('/approve'))?.body).toEqual({ review_token: 'saved-valid-version' })
})

test('workspace switching waits for a pending approval to finish', async ({ page }) => {
  let releaseApproval: () => void = () => undefined
  const approvalGate = new Promise<void>((resolve) => { releaseApproval = resolve })
  const record = sampleRecord('pending-approval', 'Pending Approval Contact', {
    draft: { id: 'draft-approval', body: 'Hi, is there an update on the next steps?', status: 'pending', review_token: 'approval-version', validation_errors: [], outbox_id: null },
  })
  const requests = await connectMock(page, { records: [record], approvalGate })
  await page.getByRole('button', { name: 'Approve to outbox', exact: true }).click()
  await expect.poll(() => requests.some((request) => request.path.endsWith('/approve'))).toBe(true)
  await page.getByRole('button', { name: 'Workspace connection settings', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Workspace connection', exact: true })).toHaveCount(0)
  await expect(page.getByRole('alert')).toContainText('Wait for the current action to finish')
  await expect(page.getByRole('button', { name: 'Engine connected', exact: true })).toBeVisible()
  await expect(panel(page).getByRole('heading', { name: 'Pending Approval Contact', exact: true })).toBeVisible()
  releaseApproval()
  await expect.poll(() => record.draft?.status).toBe('approved')
  await expect(page.getByRole('button', { name: 'Refresh queue', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Workspace connection settings', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Setup', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Lock access', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Setup required', exact: true })).toBeVisible()
  await expect(panel(page)).toHaveCount(0)
})
