import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDemoClient, createHttpClient, validateDraft } from './client'
import { DEMO_AS_OF, DEMO_DRAFT_BODIES, DEMO_STORAGE_KEY } from './fixtures'
import type { StorageLike, UIRecord } from './types'

function memoryStorage(): StorageLike {
  const entries = new Map<string, string>()
  return {
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => { entries.set(key, value) },
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('synthetic review client', () => {
  it('is explicit, frozen, source-neutral, and never fetches', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const client = createDemoClient(null)
    const page = await client.listRecords()
    expect(client.mode).toBe('demo')
    expect(page.as_of).toBe(DEMO_AS_OF)
    expect(page.items).toHaveLength(15)
    expect(page.items.filter((record) => record.draft)).toHaveLength(1)
    expect(page.items.some((record) => record.kind === 'music')).toBe(true)
    expect(page.items.some((record) => record.kind === 'community_partnership')).toBe(true)
    expect(page.items.every((record) => !record.referenced_record_ids.includes(record.id))).toBe(true)
    expect(page.items.filter((record) => record.kind === 'job_application')
      .every((record) => !record.fields.some((field) => field.label === 'Value'))).toBe(true)
    await client.sync()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('persists reviewed copy and blocks another tab from reviewing stale copy', async () => {
    const storage = memoryStorage()
    const first = createDemoClient(storage)
    const second = createDemoClient(storage)
    const initial = await first.draft('job-northstar-backend')
    const saved = await first.edit(initial.id, 'Hi Maya, is there an update on the Backend Engineer role?', initial.review_token)
    expect(saved.review_token).not.toBe(initial.review_token)
    await expect(second.approve(initial.id, initial.review_token)).rejects.toMatchObject({ code: 'stale_review', status: 409 })
    expect((await second.draft('job-northstar-backend')).body).toBe(saved.body)
    expect(storage.getItem(DEMO_STORAGE_KEY)).toContain(saved.body)
  })

  it('approves once into an unsent outbox without linking another role by company alone', async () => {
    const client = createDemoClient(null)
    const before = (await client.listRecords()).items.find((record) => record.id === 'job-northstar-platform')
    const draft = await client.draft('job-northstar-backend')
    const approved = await client.approve(draft.id, draft.review_token)
    const retry = await client.approve(draft.id, draft.review_token)
    expect(retry.outbox_id).toBe(approved.outbox_id)
    expect(await client.listOutbox()).toEqual([expect.objectContaining({
      body: draft.body, status: 'pending', sent_at: null, authorization_mode: 'human',
      contact_address: 'maya@northstar.example',
      opportunity_ids: ['job-northstar-backend'],
    })])
    const records = (await client.listRecords()).items
    expect(records.find((record) => record.id === 'job-northstar-backend')?.status).toBe('open')
    expect(records.find((record) => record.id === 'job-northstar-platform')).toEqual(before)
    expect(records.find((record) => record.id === 'job-northstar-platform')?.next_action).toBe('blocked')
  })

  it('applies contact-wide suppression to a historical pending draft on a grouped sibling', async () => {
    const storage = memoryStorage()
    const client = createDemoClient(storage)
    const first = await client.draft('sales-polaris-renewal')
    const snapshot = JSON.parse(storage.getItem(DEMO_STORAGE_KEY)!) as { records: UIRecord[] }
    const related = snapshot.records.find((record) => record.id === 'sales-polaris-analytics')!
    expect(related.candidate_id).toBeNull()
    expect(related.next_action).toBe('waiting')
    related.draft = {
      id: 'historical-analytics-draft', body: 'Hi Alex, checking in on the analytics discussion.',
      status: 'pending', review_token: 'historical-version', validation_errors: [], outbox_id: null,
    }
    storage.setItem(DEMO_STORAGE_KEY, JSON.stringify(snapshot))
    const sibling = await client.draft('sales-polaris-analytics')
    await client.approve(first.id, first.review_token)
    await expect(client.approve(sibling.id, sibling.review_token)).rejects.toMatchObject({ status: 409 })
    expect(await client.listOutbox()).toHaveLength(1)
    expect((await client.listOutbox())[0].opportunity_ids).toEqual(['sales-polaris-renewal', 'sales-polaris-analytics'])
    expect(await client.getRecord('sales-polaris-analytics')).toMatchObject({ candidate_id: null, next_action: 'waiting', reason: { code: 'cooldown' }, draft: { status: 'pending' } })
  })

  it('rejects only the draft and leaves the record open', async () => {
    const client = createDemoClient(null)
    const draft = await client.draft('job-aster-platform')
    await client.reject(draft.id, draft.review_token)
    const record = (await client.listRecords()).items.find((item) => item.id === 'job-aster-platform')
    expect(record?.status).toBe('open')
    expect(record?.draft?.status).toBe('rejected')
    expect(await client.listOutbox()).toHaveLength(0)
    await expect(client.approve(draft.id, draft.review_token)).rejects.toMatchObject({ code: 'already_reviewed' })
  })

  it('blocks missing recipients, waiting and closed cases from drafting', async () => {
    const client = createDemoClient(null)
    await expect(client.draft('job-northstar-platform')).rejects.toMatchObject({ code: 'missing_contact' })
    await expect(client.draft('job-harbor-infra')).rejects.toMatchObject({ code: 'not_eligible' })
    await expect(client.draft('job-cedar-engineer')).rejects.toMatchObject({ code: 'closed' })
  })

  it('validates saved copy without losing the previous draft on failure', async () => {
    const client = createDemoClient(null)
    const draft = await client.draft('job-northstar-backend')
    await expect(client.edit(draft.id, '   ', draft.review_token)).rejects.toMatchObject({ status: 422 })
    await expect(client.edit(draft.id, 'x'.repeat(321), draft.review_token)).rejects.toMatchObject({ status: 422 })
    expect(await client.draft('job-northstar-backend')).toEqual(draft)
    expect(validateDraft('😀'.repeat(320))).toEqual([])
    expect(Object.values(DEMO_DRAFT_BODIES).every((body) => validateDraft(body).length === 0)).toBe(true)
  })

  it('isolates returned objects and resets fictional edits only on request', async () => {
    const client = createDemoClient(memoryStorage())
    const page = await client.listRecords()
    page.items[0].title = 'External mutation'
    expect((await client.listRecords()).items[0].title).toBe('Backend Engineer')
    const draft = await client.draft('job-northstar-backend')
    await client.approve(draft.id, draft.review_token)
    await client.resetDemo?.()
    expect(await client.listOutbox()).toHaveLength(0)
    expect((await client.draft('job-northstar-backend')).status).toBe('pending')
  })

  it('retains session edits if browser storage stops accepting writes', async () => {
    const storage = memoryStorage()
    const write = storage.setItem
    let writes = 0
    storage.setItem = (key, value) => {
      if (++writes > 1) throw new Error('Quota exceeded')
      write(key, value)
    }
    const client = createDemoClient(storage)
    const initial = await client.draft('job-northstar-backend')
    const persisted = await client.edit(initial.id, 'First saved version.', initial.review_token)
    await client.edit(initial.id, 'Second version kept in this session.', persisted.review_token)
    expect((await client.draft('job-northstar-backend')).body).toBe('Second version kept in this session.')
  })

  it('keeps human replies visible during cooldown and excludes automated acknowledgments', async () => {
    const client = createDemoClient(null)
    const inbox = await client.listInbox()
    const records = (await client.listRecords()).items
    const devon = inbox.items.find((item) => item.contact_key === 'demo:devon.reed')
    expect(devon).toMatchObject({ last_outbound_at: '2026-09-08T15:00:00Z', latest_reply_at: '2026-09-09T10:00:00Z' })
    expect(records.find((record) => record.id === 'sales-fieldwork-onboarding')?.next_action).toBe('waiting')
    expect(inbox.items.some((item) => item.contact_key === 'demo:sam.rivera')).toBe(false)
    expect(inbox.items.some((item) => item.contact_key === 'demo:drew.nguyen')).toBe(false)
    expect(inbox.outreach_eligibility).toBe('not_evaluated')
  })

  it('does not treat an unsent approval or a rejected draft as an answer', async () => {
    const client = createDemoClient(null)
    const jordan = await client.draft('job-aster-platform')
    const taylor = await client.draft('job-meridian-design')
    await client.approve(jordan.id, jordan.review_token)
    await client.reject(taylor.id, taylor.review_token)
    const inbox = await client.listInbox()
    expect(inbox.items.find((item) => item.contact_key === 'demo:jordan.ellis')).toMatchObject({ pending_outbox_count: 1 })
    expect(inbox.items.find((item) => item.contact_key === 'demo:taylor.brooks')).toMatchObject({ pending_outbox_count: 0 })
    expect(inbox.items.some((item) => item.contact_key === 'demo:maya.chen')).toBe(false)
  })

  it('routes grouped replies to the current candidate for the same confirmed contact', async () => {
    const client = createDemoClient(null)
    const inbox = await client.listInbox()
    const alex = inbox.items.find((item) => item.contact_key === 'demo:alex.morgan')
    expect(alex?.review_record_id).toBe('sales-polaris-renewal')
    expect(alex?.record_refs).toEqual([
      { id: 'sales-polaris-analytics', kind: 'sales', title: 'Analytics add-on' },
      { id: 'sales-polaris-renewal', kind: 'sales', title: 'Workspace renewal' },
    ])
    expect(inbox.items.find((item) => item.contact_key === 'demo:devon.reed')?.review_record_id).toBe('sales-fieldwork-onboarding')
    expect(inbox.items.some((item) => item.review_record_id === 'job-northstar-platform')).toBe(false)
    const actionable = (await client.listRecords()).items.filter((record) => record.candidate_id && ['reply', 'follow_up'].includes(record.next_action))
    expect(new Set(actionable.map((record) => record.contact?.key)).size).toBe(actionable.length)
  })

  it('retains context references and detail access for closed records outside the first page', async () => {
    const storage = memoryStorage()
    const client = createDemoClient(storage)
    const draft = await client.draft('sales-polaris-renewal')
    await client.approve(draft.id, draft.review_token)
    const snapshot = JSON.parse(storage.getItem(DEMO_STORAGE_KEY)!) as { records: UIRecord[] }
    const primary = snapshot.records.find((record) => record.id === 'sales-polaris-renewal')!
    primary.status = 'closed'
    primary.next_action = 'closed'
    snapshot.records.unshift(...Array.from({ length: 55 }, (_, index): UIRecord => ({
      ...structuredClone(primary), id: `filler-${index}`, contact: null, draft: null,
      candidate_id: null, referenced_record_ids: [], activities: [],
    })))
    storage.setItem(DEMO_STORAGE_KEY, JSON.stringify(snapshot))
    const page = await client.listRecords()
    expect(page.items).toHaveLength(50)
    expect(page.items.some((record) => record.id === primary.id)).toBe(false)
    expect(page.has_more).toBe(true)
    expect(await client.getRecord(primary.id)).toMatchObject({ id: primary.id, kind: 'sales', status: 'closed' })
    expect((await client.listOutbox())[0].record_refs).toEqual([
      { id: 'sales-polaris-renewal', kind: 'sales', title: 'Workspace renewal' },
      { id: 'sales-polaris-analytics', kind: 'sales', title: 'Analytics add-on' },
    ])
    expect((await client.listInbox()).items.find((item) => item.contact_key === 'demo:jordan.ellis')?.record_refs)
      .toEqual([{ id: 'job-aster-platform', kind: 'job_application', title: 'Platform Engineer' }])
    await expect(client.getRecord('does-not-exist')).rejects.toMatchObject({ status: 404, code: 'not_found' })
  })
})

describe('HTTP review client', () => {
  it('sends the operator credential in a header and review versions in request bodies', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'draft-1' }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    const client = createHttpClient('http://localhost:8000/', 'operator-secret')
    await client.edit('draft/1', 'Reviewed copy', 'displayed-version')
    expect(fetch).toHaveBeenCalledWith('http://localhost:8000/v1/ui/drafts/draft%2F1/edit', {
      method: 'POST', headers: { Accept: 'application/json', Authorization: 'Bearer operator-secret', 'Content-Type': 'application/json' },
      body: JSON.stringify({ body: 'Reviewed copy', review_token: 'displayed-version' }),
    })
  })

  it('preserves backend errors instead of substituting demo results', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Recipient changed; refresh this draft.' }), { status: 409 }))
    vi.stubGlobal('fetch', fetch)
    const client = createHttpClient('', 'operator-secret')
    await expect(client.approve('draft-1', 'old-version')).rejects.toMatchObject({
      status: 409, message: 'Recipient changed; refresh this draft.',
    })
    expect(client.mode).toBe('live')
  })

  it('distinguishes an unavailable API from an empty queue', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Network error')))
    await expect(createHttpClient().listRecords()).rejects.toMatchObject({ code: 'network_error' })
  })

  it('loads a record directly without requiring a list-page cache', async () => {
    const record = await createDemoClient(null).getRecord('job-northstar-platform')
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(record), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    const client = createHttpClient('', 'operator-secret')
    expect(await client.getRecord('outside/page')).toEqual(record)
    expect(fetch).toHaveBeenCalledWith('/v1/ui/records/outside%2Fpage', {
      method: 'GET', headers: { Accept: 'application/json', Authorization: 'Bearer operator-secret' },
    })
  })

  it('sends sync defaults and preserves explicit record pagination', async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}', { status: 200 })))
    vi.stubGlobal('fetch', fetch)
    const client = createHttpClient()
    await client.sync()
    await client.listRecords(50)
    expect(fetch.mock.calls[0][0]).toBe('/v1/ui/sync')
    expect(fetch.mock.calls[0][1].body).toBe('{}')
    expect(fetch.mock.calls[1][0]).toBe('/v1/ui/records?limit=50&offset=50')
  })
})
