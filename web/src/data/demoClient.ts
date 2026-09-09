import { createFixtures, DEMO_AS_OF, DEMO_CONFIG, DEMO_DRAFT_BODIES, DEMO_STORAGE_KEY } from './fixtures'
import type { InboxItem, InboxResult, OutboxItem, RecordPage, RecordRef, ReviewClient, StorageLike, SyncResult, UIConfig, UIDraft, UIRecord } from './types'

import { ClientError, validateDraft } from './client'

interface DemoState {
  version: 1
  records: UIRecord[]
  outbox: OutboxItem[]
  next_outbox_id: number
  next_version: number
  sync_count: number
}

function initialState(): DemoState {
  return { version: 1, records: createFixtures(), outbox: [], next_outbox_id: 1, next_version: 2, sync_count: 0 }
}

function defaultStorage(): StorageLike | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

function isDemoState(value: unknown): value is DemoState {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<DemoState>
  return item.version === 1 && Array.isArray(item.records) && Array.isArray(item.outbox)
    && typeof item.next_outbox_id === 'number' && typeof item.next_version === 'number'
    && typeof item.sync_count === 'number'
    && item.records.every((record) => typeof record.id === 'string' && Array.isArray(record.activities))
}

function recordReferences(state: DemoState, ids: string[]): RecordRef[] {
  const records = new Map(state.records.map((record) => [record.id, record]))
  return [...new Set(ids)].flatMap((id) => {
    const record = records.get(id)
    return record ? [{ id: record.id, kind: record.kind, title: record.title }] : []
  })
}

function demoInbox(state: DemoState): InboxResult {
  const lastOutbounds = new Map<string, string>()
  for (const record of state.records) {
    if (!record.contact) continue
    for (const activity of record.activities) {
      if (activity.type === 'outbound' && activity.occurred_at <= DEMO_AS_OF) {
        const previous = lastOutbounds.get(record.contact.key)
        if (!previous || activity.occurred_at > previous) lastOutbounds.set(record.contact.key, activity.occurred_at)
      }
    }
  }
  const groups = new Map<string, InboxItem>()
  for (const record of state.records) {
    if (!record.contact || record.status === 'closed') continue
    const contact = record.contact
    const outbound = lastOutbounds.get(contact.key) ?? null
    const evidence = record.activities.filter((activity) => activity.type === 'inbound'
      && activity.classification === 'human' && activity.occurred_at <= DEMO_AS_OF
      && (!outbound || activity.occurred_at >= outbound))
    if (!evidence.length) continue
    const groupKey = `${contact.key}:${contact.channel}:${contact.address.toLowerCase()}`
    const item = groups.get(groupKey) ?? {
      contact_key: contact.key, contact_name: contact.name, contact_address: contact.address,
      channel: contact.channel, opportunity_ids: [], latest_reply_at: evidence[0].occurred_at,
      review_record_id: null,
      last_outbound_at: outbound, reply_evidence: [],
      pending_outbox_count: state.outbox.filter((entry) => entry.contact_key === contact.key
        && entry.status === 'pending' && entry.created_at <= DEMO_AS_OF).length,
    }
    item.opportunity_ids.push(record.id)
    for (const activity of evidence) {
      item.reply_evidence.push({ opportunity_id: record.id, activity_id: activity.id, occurred_at: activity.occurred_at, channel: contact.channel })
      if (activity.occurred_at > item.latest_reply_at) item.latest_reply_at = activity.occurred_at
    }
    item.opportunity_ids.sort()
    item.reply_evidence.sort((a, b) => a.occurred_at.localeCompare(b.occurred_at) || a.activity_id.localeCompare(b.activity_id))
    groups.set(groupKey, item)
  }
  const items = [...groups.values()].sort((a, b) => b.latest_reply_at.localeCompare(a.latest_reply_at) || a.contact_key.localeCompare(b.contact_key))
  for (const item of items) {
    item.record_refs = recordReferences(state, item.opportunity_ids)
    const primary = state.records.filter((record) => record.contact?.key === item.contact_key
      && record.status !== 'closed' && record.candidate_id && ['reply', 'follow_up'].includes(record.next_action))
      .sort((a, b) => (b.score ?? -1) - (a.score ?? -1) || a.id.localeCompare(b.id))[0]
    item.review_record_id = primary?.id ?? item.opportunity_ids[0] ?? null
  }
  return { as_of: DEMO_AS_OF, items, total: items.length, has_more: false, source_freshness: 'demo', outreach_eligibility: 'not_evaluated' }
}

/** This adapter uses fictional data only. It never invokes fetch or sends mail. */
export function createDemoClient(storage: StorageLike | null = defaultStorage()): ReviewClient {
  let state = initialState()
  let storageAvailable = storage !== null

  function read(): DemoState {
    if (!storageAvailable) return state
    try {
      const saved = storage?.getItem(DEMO_STORAGE_KEY)
      if (saved) {
        const parsed: unknown = JSON.parse(saved)
        if (isDemoState(parsed)) state = parsed
      }
    } catch {
      // Browser privacy restrictions or damaged demo data still allow this session.
      storageAvailable = false
    }
    return state
  }

  function save(next: DemoState): void {
    state = next
    if (!storageAvailable) return
    try {
      storage?.setItem(DEMO_STORAGE_KEY, JSON.stringify(next))
    } catch {
      // In-memory demo edits remain usable when browser storage is unavailable.
      storageAvailable = false
    }
  }

  function findRecord(current: DemoState, id: string): UIRecord {
    const record = current.records.find((item) => item.id === id)
    if (!record) throw new ClientError('This record is no longer available. Refresh the queue.', 404, 'not_found')
    return record
  }

  function findDraft(current: DemoState, id: string): { record: UIRecord; draft: UIDraft } {
    const record = current.records.find((item) => item.draft?.id === id)
    if (!record?.draft) throw new ClientError('This draft is no longer available. Refresh the queue.', 404, 'not_found')
    return { record, draft: record.draft }
  }

  function verifyToken(draft: UIDraft, token: string): void {
    if (draft.review_token !== token) {
      throw new ClientError('This draft changed since you opened it. Refresh and review the latest copy before continuing.', 409, 'stale_review')
    }
  }

  function verifyBody(body: string): string {
    const errors = validateDraft(body, DEMO_CONFIG.max_draft_characters)
    if (errors.length) throw new ClientError(errors[0], 422, 'validation_error', errors)
    return body.trim()
  }

  function assertEligible(record: UIRecord): void {
    if (!record.contact) throw new ClientError('This record has no confirmed recipient. Review its linked record or update the source.', 409, 'missing_contact')
    if (record.status === 'closed' || record.next_action === 'closed') throw new ClientError('This record is closed and cannot receive a follow-up.', 409, 'closed')
    if (record.next_action !== 'reply' && record.next_action !== 'follow_up') {
      throw new ClientError(record.reason.detail || 'This contact is not currently eligible for a follow-up.', 409, 'not_eligible')
    }
  }

  return {
    mode: 'demo',
    async config() {
      return structuredClone(DEMO_CONFIG)
    },
    async listRecords(offset = 0) {
      const current = read()
      const start = Math.max(0, Math.floor(offset))
      return structuredClone({ items: current.records.slice(start, start + 50), total: current.records.length, has_more: start + 50 < current.records.length, as_of: DEMO_AS_OF })
    },
    async getRecord(recordId) {
      return structuredClone(findRecord(read(), recordId))
    },
    async sync() {
      const current = read()
      current.sync_count += 1
      save(current)
      const eligible = current.records.filter((record) => ['reply', 'follow_up'].includes(record.next_action))
      const contacts = new Set(eligible.map((record) => record.contact?.key).filter(Boolean))
      return { candidate_count: contacts.size, inserted_count: 0, run_id: `demo-sync-${current.sync_count}` }
    },
    async draft(recordId) {
      const current = read()
      const record = findRecord(current, recordId)
      if (record.draft) return structuredClone(record.draft)
      assertEligible(record)
      const body = DEMO_DRAFT_BODIES[recordId]
      if (!body) throw new ClientError('No example draft is available for this record.', 409, 'not_eligible')
      record.draft = {
        id: `draft:${recordId}`, body: verifyBody(body), status: 'pending',
        review_token: `demo:${recordId}:${current.next_version++}`, validation_errors: [], outbox_id: null,
      }
      save(current)
      return structuredClone(record.draft)
    },
    async edit(draftId, body, reviewToken) {
      const current = read()
      const { record, draft } = findDraft(current, draftId)
      verifyToken(draft, reviewToken)
      if (draft.status !== 'pending') throw new ClientError('Only pending drafts can be edited.', 409, 'already_reviewed')
      assertEligible(record)
      draft.body = verifyBody(body)
      draft.review_token = `demo:${record.id}:${current.next_version++}`
      draft.validation_errors = []
      save(current)
      return structuredClone(draft)
    },
    async approve(draftId, reviewToken) {
      const current = read()
      const { record, draft } = findDraft(current, draftId)
      verifyToken(draft, reviewToken)
      if (draft.status === 'approved') return structuredClone(draft)
      if (draft.status === 'rejected') throw new ClientError('This draft was rejected and cannot be approved.', 409, 'already_reviewed')
      assertEligible(record)
      const recipient = record.contact!
      if (current.outbox.some((entry) => entry.contact_key === recipient.key)) {
        throw new ClientError('A message is already reserved for this contact. The contact-wide cooldown applies to every linked record.', 409, 'cooldown')
      }
      const body = verifyBody(draft.body)
      const outboxId = current.next_outbox_id++
      const opportunityIds = [...new Set([record.id, ...record.referenced_record_ids])]
      current.outbox.push({
        id: outboxId, draft_id: draft.id, contact_key: recipient.key, contact_address: recipient.address,
        contact_name: recipient.name, channel: recipient.channel, opportunity_ids: opportunityIds,
        record_refs: recordReferences(current, opportunityIds),
        body, status: 'pending', authorization_mode: 'human', created_at: DEMO_AS_OF, sent_at: null,
      })
      draft.status = 'approved'
      draft.outbox_id = outboxId
      record.next_action = 'approved'
      record.reason = { code: 'approved', label: 'Approved to outbox', detail: 'The message is reserved in the unsent demo outbox. Approval does not send a message.' }
      record.activities.unshift({ id: `demo-approved-${outboxId}`, label: 'Draft approved to outbox', type: 'review', occurred_at: DEMO_AS_OF, classification: 'human', source_url: null, summary: 'Reserved for human review. This demo never sends messages.' })
      for (const related of current.records) {
        if (related.id !== record.id && related.status !== 'closed'
          && (related.contact?.key === recipient.key || record.referenced_record_ids.includes(related.id))) {
          related.next_action = 'waiting'
          related.reason = { code: 'cooldown', label: 'Contact cooldown', detail: 'An approved message is already reserved for this contact. All linked records are suppressed for the 48-hour cooldown.' }
        }
      }
      save(current)
      return structuredClone(draft)
    },
    async reject(draftId, reviewToken) {
      const current = read()
      const { record, draft } = findDraft(current, draftId)
      verifyToken(draft, reviewToken)
      if (draft.status === 'rejected') return structuredClone(draft)
      if (draft.status === 'approved') throw new ClientError('This draft is already approved and cannot be rejected.', 409, 'already_reviewed')
      draft.status = 'rejected'
      record.next_action = 'rejected'
      record.reason = { code: 'rejected', label: 'Draft rejected', detail: 'This draft was rejected. The record remains open; rejection does not close an application or opportunity.' }
      record.activities.unshift({ id: `demo-rejected-${record.id}`, label: 'Draft rejected', type: 'review', occurred_at: DEMO_AS_OF, classification: 'human', source_url: null, summary: 'The draft was rejected; the underlying record remains open.' })
      save(current)
      return structuredClone(draft)
    },
    async listOutbox() {
      const current = read()
      return structuredClone(current.outbox.map((item) => ({ ...item, record_refs: recordReferences(current, item.opportunity_ids) })))
    },
    async exportOutbox() {
      throw new ClientError('Exports require a connected engine.')
    },
    async listInbox() {
      return structuredClone(demoInbox(read()))
    },
    async resetDemo() {
      save(initialState())
    },
  }
}
