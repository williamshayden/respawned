import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, Check, LoaderCircle, Menu, RefreshCw, UserRound, X } from 'lucide-react'
import { createHttpClient } from './data/client'
import { endLocalSession, isLocalSession, LOCAL_SESSION, restoreLocalSession, type ReviewAccess } from './data/auth'
import type { RecordRef } from './data/types'
import { useWorkspaces } from './data/workspaces'
import { actionable, contextLine, type Page } from './presentation'
import { useReview } from './useReview'
import { Sidebar } from './components/Sidebar'
import { QueueList } from './components/QueueList'
import { ReviewPanel, type LocalEdit } from './components/ReviewPanel'
import { AuxiliaryViews } from './components/AuxiliaryViews'
import { SetupView } from './components/SetupView'
import { WorkspaceManager } from './components/WorkspaceManager'

export default function App() {
  const [token, setToken] = useState<ReviewAccess>(null)
  const [scope, setScope] = useState('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [workspaceRevision, setWorkspaceRevision] = useState(0)
  const client = useMemo(() => token ? createHttpClient('', token, scope === 'all' ? '' : scope) : null, [token, scope, workspaceRevision])
  const review = useReview(client, selectedId)
  const workspaces = useWorkspaces(token)
  const [page, setPage] = useState<Page>('Setup')
  const [query, setQuery] = useState('')
  const [channel, setChannel] = useState('all')
  const [view, setView] = useState('ready')
  const [sort, setSort] = useState('priority')
  const [showDetail, setShowDetail] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [edits, setEdits] = useState<Record<string, LocalEdit>>({})

  useEffect(() => {
    let active = true
    void restoreLocalSession().then(connected => {
      if (active && connected) setToken(LOCAL_SESSION)
    }).catch(error => {
      if (active) review.setError(error instanceof Error ? error.message : 'Could not reconnect the browser session.')
    })
    return () => { active = false }
  }, [])

  const workspace = workspaces.items.find(item => item.id === scope)
  const kinds = workspace?.kinds ?? []
  const scopedRecords = review.records.filter(record => !kinds.length || kinds.includes(record.kind))
  const inScope = (item: { opportunity_ids: string[]; record_refs?: RecordRef[] }) => {
    if (!kinds.length) return true
    const refs = item.record_refs ?? item.opportunity_ids.flatMap(id => review.records.find(record => record.id === id) ?? [])
    return refs.some(record => kinds.includes(record.kind)) || refs.length < item.opportunity_ids.length
  }
  const scopedOutbox = review.outbox.filter(inScope)
  const scopedInbox = (review.inbox?.items ?? []).filter(inScope)
  const visibleRecords = scopedRecords.filter(record => (view === 'all' || actionable(record)) &&
    (channel === 'all' || record.contact?.channel === channel) &&
    [record.title, record.contact?.name, record.contact?.address, contextLine(record), record.reason.label].filter(Boolean).join(' ').toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => sort === 'priority' ? 0 : (b.last_contact_at ?? '').localeCompare(a.last_contact_at ?? '') || a.id.localeCompare(b.id))
  const selected = visibleRecords.find(record => record.id === selectedId) ?? visibleRecords[0] ?? null
  const count = scopedRecords.filter(actionable).length
  const edit = selected ? edits[selected.id] : undefined
  const navigate = (destination: Page) => { setPage(destination); setSidebarOpen(false); setShowDetail(false) }
  const select = (id: string) => { review.rememberSelection(id); setSelectedId(id); setPage('Review queue'); setShowDetail(true) }
  const removeEdit = (id: string) => setEdits(previous => { const next = { ...previous }; delete next[id]; return next })
  const openConnection = () => {
    if (review.busy || review.loading) { review.setError('Wait for the current action to finish before changing the workspace connection.'); return }
    if (Object.keys(edits).length) { review.setError('Save or discard your edited drafts before changing the workspace connection.'); return }
    navigate('Setup')
  }
  const changeWorkspace = (next: string) => {
    if (review.busy) { review.setError('Wait for the current action to finish before switching workspaces.'); return }
    setScope(next); review.rememberSelection(null); setSelectedId(null); setShowDetail(false)
  }
  const connect = async (value: string) => {
    if (review.busy || Object.keys(edits).length) throw new Error('Save or discard your draft edits and wait for the current action to finish first.')
    await createHttpClient('', value).config()
    setToken(value); setScope('all'); setSelectedId(null)
  }
  const disconnect = async () => {
    if (review.busy || Object.keys(edits).length) { review.setError('Save or discard your draft edits and wait for the current action to finish first.'); return }
    if (isLocalSession(token)) {
      try { await endLocalSession() }
      catch (error) { review.setError(error instanceof Error ? error.message : 'Could not lock access.'); return }
    }
    setToken(null); setScope('all'); setSelectedId(null); setPage('Setup')
  }

  return <div className={`app ${showDetail ? 'show-detail' : ''}`}>
    <Sidebar page={page} onNavigate={navigate} scope={scope} workspaces={workspaces.items} onScope={changeWorkspace}
      counts={{ 'Review queue': count, 'Reply inbox': scopedInbox.length, Outbox: scopedOutbox.filter(item => item.status === 'pending').length }}
      connected={!!token} onConnect={openConnection} open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
    <main className="main-workspace">
      <header className="page-header"><div className="page-title"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Menu size={23} /></button>
        <div><h1>{page}</h1><p>{page === 'Setup' ? 'Connect your engine. Make it yours.' : page === 'Workspaces' ? 'Your work, organized your way.' : page === 'Review queue' ? `${count} follow-up${count === 1 ? '' : 's'} ready for a decision${review.hasMore ? ' in loaded records' : ''}` : page === 'Outbox' ? 'Every reviewed message, with a clear history.' : page === 'Reply inbox' ? 'Keep the conversation moving.' : page === 'Activity' ? 'The evidence behind each next step.' : 'Clear rules for thoughtful follow-ups.'}</p></div></div>
        <div className="header-actions">{page !== 'Setup' && page !== 'Workspaces' && <button className="button refresh-button" onClick={review.reload} disabled={!client || !!review.busy || review.loading}>
          <RefreshCw size={19} className={review.busy === 'Refreshing' ? 'spin' : ''} />Refresh queue</button>}
          <span className="review-mode"><UserRound size={21} />{review.config ? review.config.policy_mode === 'automatic' ? 'Automatic policy' : 'Human review' : 'Review policy'}</span></div>
      </header>
      {(review.error || review.message) && <div className={`feedback ${review.error ? 'is-error' : ''}`} role={review.error ? 'alert' : 'status'}>
        {review.error ? <AlertCircle size={18} /> : <Check size={18} />}<span>{review.error || review.message}</span>
        {review.error && <button className="text-button" onClick={review.retry} disabled={!!review.busy}>Reload</button>}
        <button className="icon-button" aria-label="Dismiss message" onClick={() => { review.setError(null); review.setMessage(null) }}><X size={16} /></button>
      </div>}
      {page === 'Setup' ? <SetupView token={token} connected={!!token} onConnect={connect} onDisconnect={disconnect} onOpenOutbox={() => navigate('Outbox')} onImported={() => { void review.retry(); void workspaces.reload() }} /> :
        page === 'Workspaces' ? <WorkspaceManager key={token ? 'connected' : 'disconnected'} token={token} workspaces={workspaces.items} kinds={workspaces.available_kinds} loading={workspaces.loading} error={workspaces.error}
          onSetup={() => navigate('Setup')} onOpen={id => { changeWorkspace(id); navigate('Review queue') }} onSaved={async id => { await workspaces.reload(); changeWorkspace(id ?? 'all'); setWorkspaceRevision(value => value + 1) }} /> :
        !client ? <div className="empty-state"><h2>Connect to start tracking</h2><p>Set up review access, import your records, and configure a model when you are ready to draft.</p><button className="button primary" onClick={() => navigate('Setup')}>Open setup</button></div> :
        review.loading ? <div className="loading-workspace" role="status"><LoaderCircle size={25} className="spin" /><p>Loading your workspace…</p></div> : page === 'Review queue' ?
        <div className="review-layout">
          <QueueList records={visibleRecords} selected={selected?.id ?? null} onSelect={select} query={query} onQuery={setQuery} channel={channel} onChannel={setChannel}
            view={view} onView={setView} sort={sort} onSort={setSort} total={review.total} hasMore={review.hasMore} onMore={review.more} busy={!!review.busy} />
          <ReviewPanel record={selected} records={review.records} index={visibleRecords.findIndex(record => record.id === selected?.id)} total={visibleRecords.length} config={review.config} edit={edit} busy={review.busy}
            onBack={() => setShowDetail(false)} onEdit={body => {
              if (!selected?.draft) return
              const record = selected
              if (body === record.draft!.body) { removeEdit(record.id); return }
              setEdits(previous => ({ ...previous, [record.id]: { body, token: previous[record.id]?.token ?? record.draft!.review_token, draftId: record.draft!.id } }))
            }}
            onDiscard={() => selected && removeEdit(selected.id)}
            onSave={async () => { if (!selected || !edit) return; const id = selected.id
              if (await review.operate('Saving', () => client.edit(edit.draftId, edit.body, edit.token), 'Draft changes saved.')) removeEdit(id)
            }}
            onDraft={() => selected && review.operate('Generating draft', () => client.draft(selected.id))}
            onApprove={async () => { if (!selected?.draft) return
              await review.operate('Approving', () => client.approve(selected.draft!.id, selected.draft!.review_token), 'Added to outbox. Your message is unsent.')
            }}
            onReject={async () => { if (!selected?.draft) return
              await review.operate('Rejecting', () => client.reject(selected.draft!.id, selected.draft!.review_token), 'Draft rejected. The record stays in All tracked.')
            }}
            onSkip={() => {
              const index = visibleRecords.findIndex(record => record.id === selected?.id)
              const next = visibleRecords[(index + 1) % visibleRecords.length]?.id ?? null
              review.rememberSelection(next); setSelectedId(next)
              review.setMessage('Skipped for now. Your draft stays pending.')
            }} />
        </div> : <AuxiliaryViews page={page} records={scopedRecords} outbox={scopedOutbox} inbox={scopedInbox} inboxHasMore={!!review.inbox?.has_more} config={review.config} onExport={() => client.exportOutbox()} onSelect={async id => {
          if (!await review.openRecord(id)) return
          setScope('all'); setView('all'); setQuery(''); setChannel('all'); select(id)
        }} />}
    </main>
  </div>
}
