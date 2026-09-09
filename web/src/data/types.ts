/** The UI contract is source-neutral. Unknown kinds deliberately remain valid. */
export type NextAction =
  | 'reply'
  | 'follow_up'
  | 'waiting'
  | 'blocked'
  | 'closed'
  | 'approved'
  | 'rejected'

export interface UIDraft {
  id: string
  body: string
  status: 'pending' | 'approved' | 'rejected'
  review_token: string
  validation_errors: string[]
  outbox_id: number | null
}

export interface UIActivity {
  id: string
  label: string
  type: string
  occurred_at: string
  classification: 'human' | 'automated' | 'unknown'
  source_url: string | null
  summary: string | null
}

export interface UIRecord {
  id: string
  kind: string
  title: string
  status: string
  stage: string | null
  contact: {
    key: string
    name: string | null
    address: string
    channel: 'email' | 'sms'
  } | null
  fields: { label: string; value: string }[]
  reason: { code: string; label: string; detail: string }
  next_action: NextAction
  score: number | null
  last_contact_at: string | null
  candidate_id: string | null
  referenced_record_ids: string[]
  activities: UIActivity[]
  source_freshness: string
  source_url?: string | null
  draft?: UIDraft | null
}

export interface UIConfig {
  policy_mode: 'human' | 'automatic'
  cooldown_hours: number
  max_draft_characters: number
  source_freshness: string
}

export interface RecordPage {
  items: UIRecord[]
  total: number
  has_more: boolean
  as_of: string
}

export interface SyncResult {
  candidate_count: number
  inserted_count: number
  run_id: string
}

export interface RecordRef {
  id: string
  kind: string
  title: string
}

export interface OutboxItem {
  id: number
  draft_id: string
  contact_key: string
  contact_address: string
  contact_name: string | null
  channel: 'email' | 'sms'
  opportunity_ids: string[]
  record_refs?: RecordRef[]
  body: string
  status: 'pending' | 'sent' | 'failed'
  authorization_mode: 'human' | 'automatic' | 'legacy_unknown'
  created_at: string
  sent_at: string | null
}

export interface InboxItem {
  contact_key: string
  contact_name: string | null
  channel: 'email' | 'sms'
  contact_address: string
  opportunity_ids: string[]
  record_refs?: RecordRef[]
  review_record_id: string | null
  latest_reply_at: string
  last_outbound_at: string | null
  reply_evidence: {
    opportunity_id: string
    activity_id: string
    occurred_at: string
    channel: 'email' | 'sms' | null
  }[]
  pending_outbox_count: number
}

export interface InboxResult {
  as_of: string
  items: InboxItem[]
  total: number
  has_more: boolean
  source_freshness: string
  outreach_eligibility: 'not_evaluated'
}

export interface ReviewClient {
  readonly mode: 'demo' | 'live'
  config(): Promise<UIConfig>
  listRecords(offset?: number): Promise<RecordPage>
  getRecord(recordId: string): Promise<UIRecord>
  sync(): Promise<SyncResult>
  draft(recordId: string): Promise<UIDraft>
  edit(draftId: string, body: string, reviewToken: string): Promise<UIDraft>
  approve(draftId: string, reviewToken: string): Promise<UIDraft>
  reject(draftId: string, reviewToken: string): Promise<UIDraft>
  listOutbox(): Promise<OutboxItem[]>
  listInbox(): Promise<InboxResult>
  resetDemo?(): Promise<void>
}

export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}
