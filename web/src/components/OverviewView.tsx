import { useEffect, useState } from 'react'
import { ArrowUpRight, CircleCheck, Clock3, Eye, FolderOpen, RefreshCw, Server, Settings2, TriangleAlert } from 'lucide-react'
import type { ReviewAccess } from '../data/auth'
import type { EngineConnection } from '../data/connections'
import { isWatched, readWatchPreferences, useOverview, watchKey, WATCH_STORAGE_KEY, type WorkspaceOverview } from '../data/overview'
import './overview.css'

interface Props {
  connections: EngineConnection[]
  accessById: Record<string, ReviewAccess>
  onOpen(connectionId: string, workspaceId: string): void
  onConnections(): void
}

const metrics: { key: keyof WorkspaceOverview['counts']; label: string; detail: string }[] = [
  { key: 'ready', label: 'Ready for review', detail: 'Eligible follow-ups ranked by the engine, with this workspace as their primary record.' },
  { key: 'pending_drafts', label: 'Pending drafts', detail: 'All pending drafts linked to these records, including drafts that need a fresh eligibility check.' },
  { key: 'reply_contacts', label: 'Replies waiting', detail: 'Contact and channel groups with an unanswered human reply linked to these records.' },
  { key: 'pending_outbox', label: 'Unsent in outbox', detail: 'Pending outbox messages linked to these records. Approval does not send them.' },
]
const checkedTime = (value: string) => new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' })

export function OverviewView({ connections, accessById, onOpen, onConnections }: Props) {
  const [automatic, setAutomatic] = useState(true)
  const [choosing, setChoosing] = useState(false)
  const [preferences, setPreferences] = useState(() => {
    try { return readWatchPreferences(window.localStorage.getItem(WATCH_STORAGE_KEY)) }
    catch { return {} }
  })
  const [storageUnavailable, setStorageUnavailable] = useState(false)
  const { states, refresh } = useOverview(connections, accessById, automatic)
  useEffect(() => {
    try { window.localStorage.setItem(WATCH_STORAGE_KEY, JSON.stringify(preferences)); setStorageUnavailable(false) }
    catch { setStorageUnavailable(true) }
  }, [preferences])
  const toggle = (connectionId: string, workspaceId: string, watched: boolean) => setPreferences(previous => ({ ...previous, [watchKey(connectionId, workspaceId)]: watched }))
  const loading = connections.some(connection => states[connection.id]?.loading)

  return <div className="overview-view">
    <div className="overview-toolbar"><div className="overview-controls"><button className="button" onClick={() => setChoosing(value => !value)} aria-expanded={choosing}><Eye size={16} />Choose workspaces</button>
        <button className="button" onClick={refresh} disabled={loading || !connections.some(connection => accessById[connection.id])}><RefreshCw size={16} className={loading ? 'spin' : ''} />Refresh overview</button></div>
    </div>
    <div className="overview-monitoring"><label><input type="checkbox" checked={automatic} onChange={event => setAutomatic(event.target.checked)} />Refresh every 30 seconds</label>
      <span>{automatic ? 'While this overview is visible' : 'Paused · refresh manually for current counts'}</span>
      <button className="text-button" onClick={onConnections}><Settings2 size={15} />Manage connections</button></div>
    {storageUnavailable && <p className="overview-note" role="status">This browser cannot save your workspace choices. They will last until this page reloads.</p>}
    {!connections.length && <div className="empty-state"><Server size={30} /><h2>Connect an engine to start monitoring</h2><p>Add your local or remote Respawned engine, then choose the workspaces you want to watch.</p><button className="button primary" onClick={onConnections}>Manage connections</button></div>}
    <div className="overview-engines">{connections.map(connection => {
      const state = states[connection.id]
      const stale = !!state?.error && !!state.data
      const workspaces = state?.data?.workspaces ?? []
      const watched = workspaces.filter(workspace => isWatched(preferences, connection.id, workspace.id))
      const status = state?.loading ? 'Checking…' : state?.failure === 'access' ? 'Access required' : state?.failure === 'unsupported' ? 'Update needed' : state?.error ? 'Unavailable' : state?.data ? 'Connected' : 'Checking…'
      return <section className="overview-engine" key={connection.id} aria-label={connection.name} data-engine-id={connection.id}>
        <div className="overview-engine-heading"><div className="overview-engine-identity"><span className="overview-engine-icon"><Server size={20} /></span><div><h3>{connection.name}</h3><p>{connection.baseUrl || window.location.origin}</p></div></div>
          <span className={`overview-status ${state?.error ? 'has-error' : ''}`}>{state?.error ? <TriangleAlert size={14} /> : state?.loading ? <RefreshCw size={14} className="spin" /> : <CircleCheck size={14} />}{status}</span></div>
        <div className="overview-engine-check"><Clock3 size={13} />{state?.checkedAt ? <span>{stale ? 'Last successful check' : 'Checked'} <time dateTime={state.checkedAt}>{checkedTime(state.checkedAt)}</time>{!automatic && !stale ? ' · snapshot' : ''}</span> : <span>No successful check yet</span>}</div>
        {state?.error && <div className="overview-engine-error" role="status"><p>{state.error}{stale ? ' Showing the last successful snapshot; these counts may be out of date.' : ''}</p>{state.failure === 'access' && <button className="text-button" onClick={onConnections}>Unlock engine →</button>}</div>}
        {choosing && !!workspaces.length && <fieldset className="overview-choices"><legend>Watch workspaces in {connection.name}</legend>{workspaces.map(workspace => <label key={workspace.id}><input type="checkbox" checked={isWatched(preferences, connection.id, workspace.id)} onChange={event => toggle(connection.id, workspace.id, event.target.checked)} /><span>{workspace.name}</span></label>)}</fieldset>}
        {stale && <p className="overview-stale-label">STALE SNAPSHOT</p>}
        <div className={`overview-workspace-grid ${stale ? 'is-stale' : ''}`}>{watched.map(workspace => <article className="overview-workspace" key={workspace.id} data-workspace-id={workspace.id}>
          <div className="overview-workspace-heading"><div><h4>{workspace.name}</h4><p>{workspace.description || (workspace.kinds.length ? workspace.kinds.join(', ') : 'All record types in this engine.')}</p></div>
            <button className="icon-button" aria-label={`Stop watching ${workspace.name} in ${connection.name}`} onClick={() => toggle(connection.id, workspace.id, false)} title="Stop watching"><Eye size={16} /></button></div>
          <dl className="overview-metrics">{metrics.map(metric => <div key={metric.key} title={metric.detail}><dt>{metric.label}</dt><dd>{workspace.counts[metric.key].toLocaleString()}</dd></div>)}</dl>
          <div className="overview-workspace-footer"><span>{workspace.counts.records.toLocaleString()} tracked record{workspace.counts.records === 1 ? '' : 's'}</span><button className="text-button" onClick={() => onOpen(connection.id, workspace.id)} disabled={!!state?.error}>Open workspace <ArrowUpRight size={15} /></button></div>
        </article>)}</div>
        {state?.data && !watched.length && <div className="overview-none-watched"><FolderOpen size={22} /><p>{workspaces.length ? 'No workspaces watched in this engine.' : 'No workspaces available from this engine.'}</p>{!!workspaces.length && <button className="text-button" onClick={() => setChoosing(true)}>Choose workspaces</button>}</div>}
      </section>
    })}</div>
    <details className="overview-count-help"><summary>About these counts</summary><p>Workspaces can overlap; their counts are not added together. Each count reflects the engine’s saved data at its last successful check. Source freshness is unknown.</p><dl>{metrics.map(metric => <div key={metric.key}><dt>{metric.label}</dt><dd>{metric.detail}</dd></div>)}</dl><p>Tracked records includes every record matching the workspace. Monitoring reads saved data; it does not import source changes, generate drafts, or send messages.</p></details>
  </div>
}
