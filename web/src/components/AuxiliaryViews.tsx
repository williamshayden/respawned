import { useState } from 'react'
import { ArrowUpRight, Download, Mail, ShieldCheck } from 'lucide-react'
import type { InboxItem, OutboxItem, ReviewClient, UIConfig, UIRecord } from '../data/types'
import { contextLine, shortDate, type Page } from '../presentation'
import { ActivityView } from './ActivityView'

function downloadOutbox(exported: Blob) {
  const url = URL.createObjectURL(exported)
  const link = document.createElement('a'); link.href = url; link.download = 'respawned-outbox.csv'; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

interface Props { page: Page; records: UIRecord[]; outbox: OutboxItem[]; inbox: InboxItem[]; inboxHasMore: boolean; inboxTotal: number; onMoreInbox: () => void; busy: boolean; activityClient: ReviewClient; config: UIConfig | null; onSelect: (id: string) => void; onExport: () => Promise<Blob> }
export function AuxiliaryViews({ page, records, outbox, inbox, inboxHasMore, inboxTotal, onMoreInbox, busy, activityClient, config, onSelect, onExport }: Props) {
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const exportOutbox = async () => {
    setExporting(true); setExportError(null)
    try { downloadOutbox(await onExport()) }
    catch (error) { setExportError(error instanceof Error ? error.message : 'Could not export the outbox. Please try again.') }
    finally { setExporting(false) }
  }
  if (page === 'Policy') return <div className="auxiliary-view policy-view"><div className="aux-heading"><ShieldCheck size={21} /><div><h2>Effective policy</h2><p>Applies to every workspace in this engine.</p></div></div>
    <dl className="policy-list"><div><dt>Review mode</dt><dd>{!config ? 'Unavailable' : config.policy_mode === 'human' ? 'Human review' : 'Automatic authorization'}</dd></div>
      <div><dt>Contact cooldown</dt><dd>{config?.cooldown_hours ?? '—'} hours</dd></div><div><dt>Draft length</dt><dd>{config?.max_draft_characters ?? '—'} characters maximum</dd></div>
      <div><dt>Source freshness</dt><dd>{config?.source_freshness === 'unknown' ? 'Unknown' : config?.source_freshness}</dd></div></dl>
    <p className="subtle-note">Eligibility and ranking follow the engine policy. Changing context does not change approval authority.</p>
  </div>
  if (page === 'Outbox') return <div className="auxiliary-view"><div className="aux-heading"><p>Approved messages and their recorded status. Export to send through your own tools.</p>
    <button className="button" onClick={() => void exportOutbox()} disabled={!outbox.length || exporting}><Download size={17} />{exporting ? 'Exporting…' : 'Export CSV'}</button></div>
    {exportError && <p className="feedback is-error" role="alert">{exportError}</p>}
    {!outbox.length ? <div className="empty-state"><Mail size={30} /><h2>Nothing in the outbox yet</h2><p>Approved drafts will appear here. Approval does not send a message.</p></div> :
      <div className="outbox-list">{outbox.map(item => <article key={item.id} className="outbox-item"><div className="outbox-item-top"><div><h3>{item.contact_name || item.contact_address}</h3><p>{item.contact_address}</p></div>
        <span className={`pill ${item.status === 'pending' ? 'teal' : ''}`}>{item.status === 'pending' ? 'Unsent' : item.status === 'sent' ? 'Sent' : 'Failed'}</span></div>
        <p className="message-copy">{item.body}</p><div className="outbox-meta"><span>{item.authorization_mode === 'human' ? 'Human approved' : item.authorization_mode === 'automatic' ? 'Automatically authorized' : 'Authorization unknown'}</span><span>{shortDate(item.created_at)} · {item.channel}</span></div>
      </article>)}</div>}
  </div>
  if (page === 'Reply inbox') {
    return <div className="auxiliary-view"><div className="aux-heading"><p>Human replies awaiting a response. Source freshness is unknown.</p></div>
      {inbox.length ? inbox.map(item => {
        const targetId = item.review_record_id ?? item.opportunity_ids[0]
        const record = records.find(record => record.id === targetId)
        return <button className="inbox-row" key={`${item.contact_key}:${item.channel}`} disabled={!targetId || busy} onClick={() => targetId && onSelect(targetId)}><Mail size={21} /><span>
          <strong>{item.contact_name || item.contact_address}</strong><span>{record ? contextLine(record) : item.record_refs?.map(record => record.title).join(', ') || item.opportunity_ids.join(', ')}</span>
          <span className="muted">Reply received {shortDate(item.latest_reply_at)} · {item.pending_outbox_count ? `${item.pending_outbox_count} unsent in outbox` : 'No later response recorded'}</span>
        </span><ArrowUpRight size={20} /></button>
      }) : <div className="empty-state"><Mail size={30} /><h2>No replies waiting</h2><p>Newly ingested human replies will appear here.</p></div>}
      {inboxHasMore && <div className="queue-footer"><p>{inbox.length} of {inboxTotal} reply routes loaded.</p><button className="button" onClick={onMoreInbox} disabled={busy}>{busy ? 'Loading…' : 'Load more replies'}</button></div>}
    </div>
  }
  return <ActivityView client={activityClient} onSelect={onSelect} />
}
