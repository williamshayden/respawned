import { useEffect, useMemo, useState } from 'react'
import { AlertCircle, Check, LoaderCircle, Menu, RefreshCw, UserRound, X } from 'lucide-react'
import { createHttpClient } from './data/client'
import { endLocalSession, isLocalSession, LOCAL_SESSION, restoreLocalSession } from './data/auth'
import { useConnections } from './data/connections'
import { useWorkspaces } from './data/workspaces'
import { actionable, contextLine, type Page } from './presentation'
import { useReview } from './useReview'
import { Sidebar } from './components/Sidebar'
import { QueueList } from './components/QueueList'
import { ReviewPanel, type LocalEdit } from './components/ReviewPanel'
import { AuxiliaryViews } from './components/AuxiliaryViews'
import { SetupView } from './components/SetupView'
import { WorkspaceManager } from './components/WorkspaceManager'
import { ConnectionsView } from './components/ConnectionsView'
import { OverviewView } from './components/OverviewView'

export default function App() {
  const engines = useConnections()
  const token = engines.accessById[engines.activeId] ?? null
  const baseUrl = engines.active.baseUrl
  const [scope, setScope] = useState('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [workspaceRevision, setWorkspaceRevision] = useState(0)
  const client = useMemo(() => token ? createHttpClient(baseUrl, token, scope === 'all' ? '' : scope) : null, [baseUrl, token, scope, workspaceRevision])
  const review = useReview(client, selectedId)
  const workspaces = useWorkspaces(token, baseUrl)
  const [page, setPage] = useState<Page>('Setup')
  const [setupSources, setSetupSources] = useState(false)
  const [query, setQuery] = useState('')
  const [channel, setChannel] = useState('all')
  const [view, setView] = useState('ready')
  const [sort, setSort] = useState('priority')
  const [showDetail, setShowDetail] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [edits, setEdits] = useState<Record<string, LocalEdit>>({})
  const [contextBusy, setContextBusy] = useState(false)
  const [connectionBusy, setConnectionBusy] = useState(false)

  useEffect(() => {
    let active = true
    void restoreLocalSession().then(connected => {
      if (active && connected) engines.setAccess('local', LOCAL_SESSION)
    }).catch(error => {
      if (active) review.setError(error instanceof Error ? error.message : 'Could not reconnect the browser session.')
    })
    return () => { active = false }
  }, [])

  const workspace = workspaces.items.find(item => item.id === scope)
  // Workspace filtering and contact grouping belong to the API.
  const scopedRecords = review.records
  const scopedOutbox = review.outbox
  const scopedInbox = review.inbox?.items ?? []
  const visibleRecords = scopedRecords.filter(record => (view === 'all' || actionable(record)) &&
    (channel === 'all' || record.contact?.channel === channel) &&
    [record.title, record.contact?.name, record.contact?.address, contextLine(record), record.reason.label].filter(Boolean).join(' ').toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => sort === 'priority' ? 0 : (b.last_contact_at ?? '').localeCompare(a.last_contact_at ?? '') || a.id.localeCompare(b.id))
  const selected = visibleRecords.find(record => record.id === selectedId) ?? visibleRecords[0] ?? null
  const count = scopedRecords.filter(actionable).length
  const edit = selected ? edits[selected.id] : undefined
  const navigate = (destination: Page) => {
    if (destination !== page && (contextBusy || connectionBusy)) { review.setError('Wait for the current setup or connection change to finish before leaving this page.'); return }
    setPage(destination); setSidebarOpen(false); setShowDetail(false)
    if (destination !== 'Setup') setSetupSources(false)
  }
  const openImport = () => { navigate('Setup'); setSetupSources(true) }
  const select = (id: string) => { review.rememberSelection(id); setSelectedId(id); setPage('Review queue'); setShowDetail(true) }
  const removeEdit = (id: string) => setEdits(previous => { const next = { ...previous }; delete next[id]; return next })
  const openConnection = () => {
    if (review.busy || review.loading) { review.setError('Wait for the current action to finish before changing the workspace connection.'); return }
    if (Object.keys(edits).length) { review.setError('Save or discard your edited drafts before changing the workspace connection.'); return }
    navigate('Setup')
  }
  const changeWorkspace = (next: string) => {
    if (review.busy || contextBusy || connectionBusy) { review.setError('Wait for the current action to finish before switching workspaces.'); return }
    setScope(next); review.rememberSelection(null); setSelectedId(null); setShowDetail(false)
  }
  const connectionChangeError = () => review.busy || contextBusy || connectionBusy
    ? 'Finish the current action before switching engine connections.'
    : Object.keys(edits).length ? 'Save or discard your draft edits before switching engine connections.' : null
  const changeEngine = (next: string, workspaceId = 'all', destination: Page = 'Review queue') => {
    if (contextBusy || connectionBusy) { review.setError('Finish the current action before switching engine connections.'); return }
    if (next !== engines.activeId) {
      const error = connectionChangeError()
      if (error) { review.setError(error); return }
      engines.setActiveId(next)
      setEdits({}); setQuery(''); setChannel('all'); setView('ready')
    } else if (review.busy) { review.setError('Wait for the current action to finish before switching workspaces.'); return }
    setScope(workspaceId); review.rememberSelection(null); setSelectedId(null); setShowDetail(false)
    navigate(engines.accessById[next] ? destination : 'Setup')
  }
  const connectionOperation = async <T,>(operation: () => Promise<T>): Promise<T> => {
    setConnectionBusy(true)
    try { return await operation() }
    finally { setConnectionBusy(false) }
  }
  const connect = async (value: string) => {
    if (review.busy || Object.keys(edits).length) throw new Error('Save or discard your draft edits and wait for the current action to finish first.')
    await connectionOperation(() => engines.unlock(engines.activeId, value))
    setScope('all'); setSelectedId(null)
  }
  const lockConnection = async (id: string) => {
    if (id === engines.activeId) {
      const error = connectionChangeError()
      if (error) throw new Error(error)
    }
    await connectionOperation(async () => {
      if (isLocalSession(engines.accessById[id])) await endLocalSession()
      engines.lock(id)
    })
    if (id === engines.activeId) { setScope('all'); setSelectedId(null) }
  }
  const disconnect = async () => {
    try { await lockConnection(engines.activeId); navigate('Setup') }
    catch (error) { review.setError(error instanceof Error ? error.message : 'Could not lock access.') }
  }
  const removeConnection = (id: string) => {
    if (id === engines.activeId) {
      const error = connectionChangeError()
      if (error) throw new Error(error)
      setScope('all'); setSelectedId(null); setEdits({})
    }
    engines.remove(id)
  }

  return <div className={`app ${showDetail ? 'show-detail' : ''}`}>
    <Sidebar page={page} onNavigate={navigate} scope={scope} workspaces={workspaces.items} onScope={changeWorkspace}
      connections={engines.connections} activeConnectionId={engines.activeId} onConnection={changeEngine}
      counts={{ 'Review queue': count, 'Reply inbox': scopedInbox.length, Outbox: scopedOutbox.filter(item => item.status === 'pending').length }}
      connected={!!token} onConnect={openConnection} open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
    <main className="main-workspace">
      <header className="page-header"><div className="page-title"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Menu size={23} /></button>
        <div><h1>{page}</h1>{!['Overview', 'Connections'].includes(page) && <p>{engines.active.name}{!['Setup', 'Workspaces', 'Policy'].includes(page) && ` · ${workspace?.name ?? 'All work'}`}</p>}</div></div>
        <div className="header-actions">{!['Setup', 'Workspaces', 'Connections', 'Overview'].includes(page) && <button className="button refresh-button" onClick={review.retry} disabled={!client || !!review.busy || review.loading}>
          <RefreshCw size={19} className={review.busy === 'Reloading' ? 'spin' : ''} />Refresh</button>}
          {page === 'Review queue' && <button className="button" onClick={review.reload} disabled={!client || !!review.busy || review.loading} title="Evaluate all imported records on this engine and update review candidates.">Evaluate queue</button>}
          {!['Overview', 'Connections'].includes(page) && <span className="review-mode"><UserRound size={21} />{review.config ? review.config.policy_mode === 'automatic' ? 'Automatic policy' : 'Human review' : 'Review policy'}</span>}</div>
      </header>
      {(review.error || review.message) && <div className={`feedback ${review.error ? 'is-error' : ''}`} role={review.error ? 'alert' : 'status'}>
        {review.error ? <AlertCircle size={18} /> : <Check size={18} />}<span>{review.error || review.message}</span>
        {review.error && <button className="text-button" onClick={review.retry} disabled={!!review.busy}>Reload</button>}
        <button className="icon-button" aria-label="Dismiss message" onClick={() => { review.setError(null); review.setMessage(null) }}><X size={16} /></button>
      </div>}
      {page === 'Overview' ? <OverviewView connections={engines.connections} accessById={engines.accessById} onOpen={changeEngine} onConnections={() => navigate('Connections')} /> :
        page === 'Connections' ? <ConnectionsView connections={engines.connections} accessById={engines.accessById} activeId={engines.activeId}
          onAdd={(name, url, value) => connectionOperation(() => engines.add(name, url, value))}
          onUnlock={(id, value) => connectionOperation(() => engines.unlock(id, value))}
          onLock={lockConnection} onRemove={removeConnection} onOpen={id => changeEngine(id)} /> :
        page === 'Setup' ? <SetupView key={engines.activeId} baseUrl={baseUrl} token={token} connected={!!token} onBusyChange={setContextBusy} onConnect={connect} onDisconnect={disconnect} onOpenOutbox={() => navigate('Outbox')} onOpenReview={() => navigate('Review queue')} focusSources={setupSources} onImported={() => { void review.retry(); void workspaces.reload() }} /> :
        page === 'Workspaces' ? <WorkspaceManager key={`${engines.activeId}:${!!token}`} baseUrl={baseUrl} token={token} onBusyChange={setContextBusy} workspaces={workspaces.items} kinds={workspaces.available_kinds} loading={workspaces.loading} error={workspaces.error}
          onSetup={() => navigate('Setup')} onOpen={id => { changeWorkspace(id); navigate('Review queue') }} onSaved={async id => { await workspaces.reload(); setScope(id ?? 'all'); review.rememberSelection(null); setSelectedId(null); setShowDetail(false); setWorkspaceRevision(value => value + 1) }} /> :
        !client ? <div className="empty-state"><h2>Connect to start tracking</h2><p>Set up engine access, import your records, and configure a model when you are ready to draft.</p><button className="button primary" onClick={() => navigate('Setup')}>Open setup</button></div> :
        review.loading ? <div className="loading-workspace" role="status"><LoaderCircle size={25} className="spin" /><p>Loading your workspace…</p></div> : page === 'Review queue' ?
        <div className="review-layout">
          <QueueList records={visibleRecords} selected={selected?.id ?? null} onSelect={select} query={query} onQuery={setQuery} channel={channel} onChannel={setChannel}
            view={view} onView={setView} sort={sort} onSort={setSort} onImport={openImport} onClearFilters={() => { setQuery(''); setChannel('all') }} total={review.total} hasMore={review.hasMore} onMore={review.more} busy={!!review.busy} />
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
