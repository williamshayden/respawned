import type { RecordPage, RecordQuery, UIRecord } from './types'
import { actionable, contextLine } from '../presentation'

/** Legacy engines need a complete record set before client-side filtering. */
export function matchesRecordQuery(record: UIRecord, query: RecordQuery): boolean {
  return (query.view !== 'ready' || actionable(record)) && (!query.channel || record.contact?.channel === query.channel) &&
    [record.title, record.contact?.name, record.contact?.address, contextLine(record), record.reason.label]
      .filter(Boolean).join(' ').toLowerCase().includes((query.search ?? '').toLowerCase())
}

export function queryRecords(records: UIRecord[], query: RecordQuery, offset: number, asOf: string): RecordPage {
  const filtered = records.filter(record => matchesRecordQuery(record, query))
  if (query.sort === 'recent') filtered.sort((a, b) => (b.last_contact_at ?? '').localeCompare(a.last_contact_at ?? '') || a.id.localeCompare(b.id))
  return { items: filtered.slice(offset, offset + 50), total: filtered.length, has_more: offset + 50 < filtered.length, as_of: asOf,
    counts: { tracked: records.length, ready: records.filter(actionable).length } }
}
