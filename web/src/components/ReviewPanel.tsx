import { useLayoutEffect, useRef } from 'react'
import { ArrowLeft, ArrowRight, CalendarDays, Check, CircleCheck, ExternalLink, LoaderCircle, Mail, MessageSquare, PencilLine, Save, SkipForward, Sparkles, X } from 'lucide-react'
import type { UIConfig, UIRecord } from '../data/types'
import { actionLabel, actionable, displayName, safeSource, shortDate } from '../presentation'

export type LocalEdit = { kind: 'new'; body: string } | { kind: 'existing'; body: string; token: string; draftId: string }
interface Props {
  record: UIRecord | null; index: number; total: number; config: UIConfig | null
  edit?: LocalEdit; onEdit: (body: string) => void; onSave: () => void; onDiscard: () => void
  onWrite: () => void; canWriteDraft: boolean
  onDraft: () => void; onApprove: () => void; onReject: () => void; onSkip: () => void; onBack: () => void
  busy: string | null; records: UIRecord[]
}

const activityDisclosure = (type: string) => ['inbound', 'outbound', 'message_sent', 'contact_replied', 'message_received'].includes(type) ? 'View message' : 'View activity'

export function ReviewPanel(props: Props) {
  const { record, busy } = props
  const scroll = useRef<HTMLDivElement>(null)
  const writing = props.edit?.kind === 'new'
  useLayoutEffect(() => { if (scroll.current) scroll.current.scrollTop = 0 }, [record?.id])
  useLayoutEffect(() => {
    if (writing && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
  }, [record?.id, writing])
  if (!record) return <section className="review-pane empty-detail"><div className="empty-state"><CircleCheck size={36} />
    <h2>No record selected</h2><p>Select a record to review its context and next action.</p></div></section>
  const draft = record.draft
  const newDraftConflict = writing && !!draft
  const body = props.edit?.body ?? draft?.body ?? ''
  const dirty = !!props.edit
  const max = props.config?.max_draft_characters ?? 320
  const length = Array.from(body.trim()).length
  const invalid = !body.trim() || length > max
  const validationFailed = (writing ? !!body && invalid : invalid) || !!draft?.validation_errors.length
  const pending = draft?.status === 'pending'
  const canEdit = writing || (pending && record.status === 'open' && !!record.contact)
  const canReview = actionable(record) && pending && !draft.validation_errors.length
  const latestActivities = [...record.activities].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at) || a.id.localeCompare(b.id)).slice(-3)
  const referenced = record.referenced_record_ids.filter(id => id !== record.id)
    .map(id => props.records.find(item => item.id === id)?.title ?? id)
  return <section className="review-pane" aria-label="Selected record">
    <div className="review-scroll" ref={scroll}>
      <button className="text-button mobile-back" onClick={props.onBack}><ArrowLeft size={17} />Back to records</button>
      <div className="review-position">{props.index > -1 ? `${props.index + 1} of ${props.total}` : 'Tracked record'}</div>
      {safeSource(record.source_url) && <a className="record-source" href={safeSource(record.source_url)} target="_blank" rel="noreferrer">View source<ExternalLink size={13} /></a>}
      <div className="contact-heading"><h2>{displayName(record)}</h2>
        <span className={`pill ${record.status === 'open' ? 'teal' : ''}`} title="Record stage">{record.stage || (record.status === 'open' ? 'Open' : 'Closed')}</span>
      </div>
      <div className="contact-route">
        <span>{record.contact?.address || 'Human contact not identified'}</span>
        {record.contact && <span className="pill channel">{record.contact.channel === 'email' ? <Mail size={14} /> : <MessageSquare size={14} />}{record.contact.channel === 'email' ? 'Email' : 'SMS'}</span>}
      </div>
      <dl className="context-fields">{(record.fields.length ? record.fields : [{ label: 'Record', value: record.title }]).map(field =>
        <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}
      </dl>
      {referenced.length > 0 && <p className="related-records">Also in this follow-up: {referenced.join(', ')}</p>}
      <section className="reason-panel" aria-labelledby="reason-heading">
        <h3 id="reason-heading">Why this follow-up</h3><p>{record.reason.detail}</p>
        <div className="evidence-chips">
          <span className="evidence-chip"><CalendarDays size={17} />Last contact · {shortDate(record.last_contact_at)}</span>
          <span className="evidence-chip"><CircleCheck size={17} />{actionable(record) ? 'Cooldown clear' : actionLabel(record)}</span>
        </div>
        {record.source_freshness !== 'demo' && <p className="source-freshness">{record.source_freshness === 'unknown' ? 'Source freshness unknown' : record.source_freshness}</p>}
      </section>
      <section className="activity-section" aria-labelledby="activity-heading">
        <h3 id="activity-heading">Recent activity</h3>
        {latestActivities.length ? <ol className="timeline">{latestActivities.map(activity => <li key={activity.id}>
          <span className="timeline-dot" /><time dateTime={activity.occurred_at}>{shortDate(activity.occurred_at)}</time>
          <span className="activity-label">{activity.label}{safeSource(activity.source_url) && <a href={safeSource(activity.source_url)} target="_blank" rel="noreferrer" aria-label={`Source for ${activity.label}`}><ExternalLink size={13} /></a>}</span>
          {activity.classification !== 'unknown' && <span className="activity-classification">{activity.classification === 'human' ? 'Human' : 'Automated'}</span>}
          {activity.summary?.trim() && <details className="activity-message"><summary aria-label={`${activityDisclosure(activity.type)} for ${activity.label} on ${shortDate(activity.occurred_at)}`}>{activityDisclosure(activity.type)}</summary><p>{activity.summary}</p></details>}
        </li>)}</ol> : <p className="muted">No source activity available.</p>}
      </section>
      <section className="draft-panel" aria-labelledby="draft-heading">
        <div className="draft-header"><h3 id="draft-heading">Draft message</h3></div>
        {draft || writing ? <>
          <textarea aria-label="Draft message" value={body} onChange={event => props.onEdit(event.target.value)}
            readOnly={!canEdit || !!busy} aria-invalid={validationFailed} spellCheck="true" autoFocus={writing} />
          <div className="draft-status-row">
            <span className={`validation ${validationFailed ? 'invalid' : ''}`}>{validationFailed ? <X size={16} /> : <Check size={16} />}
              {newDraftConflict ? 'A saved draft is now available' : invalid ? !body.trim() ? 'Write a message to save' : `${length - max} characters over the limit` : writing ? 'Unsaved draft' : dirty ? 'Unsaved changes' : draft?.validation_errors.length ? 'Validation needs attention' : draft?.status === 'pending' ? 'Validation passed' : draft?.status === 'approved' ? 'Added to outbox' : 'Draft rejected'}
            </span>
            {dirty && <span className="character-count">{length} / {max}</span>}
          </div>
          {dirty && <div className="edit-actions"><button className="text-button" onClick={props.onDiscard} disabled={!!busy}>{writing ? 'Discard draft' : 'Discard changes'}</button>
            <button className={`button small ${writing ? 'primary' : ''}`} disabled={invalid || !!busy || (writing && (!props.canWriteDraft || !actionable(record) || newDraftConflict))} onClick={props.onSave}><Save size={16} />{writing ? 'Save draft' : 'Save changes'}</button></div>}
          {newDraftConflict && <div className="manual-draft-conflict"><p>Your unsaved text is kept above. Discard it when you are ready to review the saved draft.</p><details><summary>View saved draft</summary><p>{draft.body}</p></details></div>}
          {!!draft?.validation_errors.length && <p className="inline-error" role="alert">{draft.validation_errors.join(' ')}</p>}
        </> : <div className="draft-placeholder">
          <p>{actionable(record) ? props.canWriteDraft ? 'Write your message, or generate one with your configured model.' : 'Generate a draft using this record’s context.' : !record.contact ? 'A human contact is required to write a draft.' : 'This record is not ready for outreach.'}</p>
          {actionable(record) && <div className="draft-start-actions">
            {props.canWriteDraft && <button className="button primary" onClick={props.onWrite} disabled={!!busy}><PencilLine size={17} />Write draft</button>}
            <button className="button" onClick={props.onDraft} disabled={!!busy}>{busy === 'Generating draft' ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}Generate draft</button>
          </div>}
          {actionable(record) && !props.canWriteDraft && <p className="muted">Update this engine to Respawned 2.0 or newer to write a draft yourself.</p>}
        </div>}
      </section>
    </div>
    <footer className="decision-bar"><p>{writing ? 'Save your draft before approval. Writing makes no model request.' : dirty ? 'Save your changes before approval.' : 'Approval adds this draft to the unsent outbox.'}</p>
      <div className="decision-buttons">
        <button className="button" onClick={props.onReject} disabled={!pending || dirty || !!busy}><X size={18} />Reject</button>
        <button className="button" onClick={props.onSkip} disabled={!!busy}><SkipForward size={18} />Skip</button>
        <button className="button primary" onClick={props.onApprove} disabled={!canReview || dirty || invalid || !!busy}>
          {busy === 'Approving' ? <LoaderCircle size={17} className="spin" /> : null}Approve to outbox<ArrowRight size={19} />
        </button>
      </div>
    </footer>
  </section>
}
