vi.mock('./workflow', () => ({ workflowRoot: async (baseUrl = '') => `${baseUrl}/v1/workflow` }))
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createHttpClient } from './client'
import { createFixtures, DEMO_AS_OF } from './fixtures'
import { matchesRecordQuery, queryRecords } from './recordQuery'
import type { UIRecord } from './types'

afterEach(() => vi.unstubAllGlobals())
function records(): UIRecord[] {
  const template = createFixtures().find(record => record.next_action === 'reply' && record.contact && record.candidate_id)!
  return Array.from({ length: 205 }, (_, index) => ({ ...structuredClone(template), id: `audit-${index + 1}`,
    contact: { ...template.contact!, name: `Audit Contact ${index + 1}` } }))
}

describe('complete queue queries', () => {
  it('uses server filtering before pagination and trusts its workspace counts without eager page fetching', async () => {
    const match = records()[204]
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [match], total: 1, has_more: false, as_of: DEMO_AS_OF, counts: { tracked: 205, ready: 205 } })))
    vi.stubGlobal('fetch', fetch)
    const result = await createHttpClient('', 'fixture-token', 'sales-workspace').listRecords(0, { search: 'Audit Contact 205', channel: 'email', view: 'ready', sort: 'recent' })
    expect(result.items.map(record => record.id)).toEqual(['audit-205'])
    expect(result.counts).toEqual({ tracked: 205, ready: 205 })
    expect(fetch).toHaveBeenCalledOnce()
    const query = new URL(fetch.mock.calls[0][0], 'http://fixture.local').searchParams
    expect(Object.fromEntries(query)).toEqual({ limit: '50', offset: '0', workspace_id: 'sales-workspace', search: 'Audit Contact 205', channel: 'email', view: 'ready', sort: 'recent' })
  })

  it('finds a fifth-page record on a legacy engine that ignores query filters', async () => {
    const all = records()
    const fetch = vi.fn(async (url: string) => {
      const offset = Number(new URL(url, 'http://fixture.local').searchParams.get('offset'))
      return new Response(JSON.stringify({ items: all.slice(offset, offset + 50), total: all.length, has_more: offset + 50 < all.length, as_of: DEMO_AS_OF }))
    })
    vi.stubGlobal('fetch', fetch)
    const page = await createHttpClient('', 'fixture-token').listRecords(0, { search: 'Audit Contact 205', view: 'ready' })
    expect(page.items.map(record => record.id)).toEqual(['audit-205'])
    expect(page).toMatchObject({ total: 1, has_more: false, counts: { tracked: 205, ready: 205 } })
    expect(fetch).toHaveBeenCalledTimes(5)
  })

  it('keeps ready totals independent of search and requires a contact and candidate', () => {
    const all = records().slice(0, 3)
    all[1].candidate_id = null
    all[2].contact = null
    expect(queryRecords(all, { view: 'ready', search: 'missing' }, 0, DEMO_AS_OF)).toMatchObject({ items: [], total: 0, counts: { tracked: 3, ready: 1 } })
  })

  it('does not pin an approved or rejected direct detail back into the ready queue', () => {
    const record = records()[0]
    record.next_action = 'approved'
    expect(matchesRecordQuery(record, { view: 'ready' })).toBe(false)
    expect(matchesRecordQuery(record, { view: 'all' })).toBe(true)
    record.next_action = 'rejected'
    expect(matchesRecordQuery(record, { view: 'ready' })).toBe(false)
  })

  it('requests the next inbox page with the same workspace and credentials', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"items":[],"total":206,"has_more":false}'))
    vi.stubGlobal('fetch', fetch)
    await createHttpClient('', 'fixture-token', 'sales-workspace').listInbox(200)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/workflow/inbox?limit=200&offset=200&workspace_id=sales-workspace', expect.objectContaining({ headers: { Accept: 'application/json', Authorization: 'Bearer fixture-token' } }))
  })
})
