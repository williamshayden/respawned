import { useEffect, useState } from 'react'
import { LoaderCircle, Plus, Save, Trash2 } from 'lucide-react'
import { workspaceRequest, type Workspace, type WorkspaceInput } from '../data/workspaces'
import type { ReviewAccess } from '../data/auth'
import './workspaces.css'

interface Props {
  baseUrl?: string
  token: ReviewAccess
  workspaces: Workspace[]
  kinds: string[]
  loading: boolean
  error: string | null
  onSaved(id?: string): Promise<void>
  onOpen(id: string): void
  onSetup(): void
  onBusyChange?(busy: boolean): void
}
const blank: WorkspaceInput = { name: '', description: '', kinds: [] }

export function WorkspaceManager(props: Props) {
  const [editing, setEditing] = useState<string | null>(null)
  const [form, setForm] = useState<WorkspaceInput>(blank)
  const [kind, setKind] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const onBusyChange = props.onBusyChange
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false) }, [busy, onBusyChange])
  const allKinds = [...new Set([...props.kinds, ...form.kinds])].sort()
  const select = (workspace?: Workspace) => {
    setEditing(workspace?.id ?? null)
    setForm(workspace ? { name: workspace.name, description: workspace.description, kinds: [...workspace.kinds] } : blank)
    setKind(''); setError(null); setMessage(null); setDeleting(false)
  }
  const save = async () => {
    setBusy(true); setError(null); setMessage(null)
    try {
      const result = await workspaceRequest<Workspace>(props.token, editing ? `/${editing}` : '', editing ? 'PUT' : 'POST', form, props.baseUrl)
      setEditing(result.id); setForm({ name: result.name, description: result.description, kinds: result.kinds })
      await props.onSaved(result.id); setMessage('Workspace saved to your engine.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save workspace.') }
    finally { setBusy(false) }
  }
  return <div className="workspace-management">
    <p className="workspace-intro">Group records into workspaces by type. Workspaces share this engine’s records, model connection, and review policy.</p>
    {!props.token ? <div className="empty-state"><h2>Connect your engine first</h2><p>Workspaces are saved in the database so they are available the next time you connect.</p><button className="button primary" onClick={props.onSetup}>Open setup</button></div> : <>
      {props.error && <p role="alert" className="inline-error">{props.error}</p>}
      <div className="workspace-manager-grid"><div className="workspace-cards">
        <button className="button new-workspace" onClick={() => select()} disabled={busy}><Plus size={18} />New workspace</button>
        {props.loading && <p className="muted" role="status">Loading workspaces…</p>}
        {!props.loading && !props.workspaces.length && <p className="muted">No saved workspaces.</p>}
        {props.workspaces.map(workspace => <article key={workspace.id} className={`workspace-card ${editing === workspace.id ? 'is-active' : ''}`}>
          <button onClick={() => select(workspace)} disabled={busy}><h3>{workspace.name}</h3>{workspace.description && <p>{workspace.description}</p>}<span>{workspace.kinds.length ? workspace.kinds.join(', ') : 'All record types'}</span></button>
          <button className="text-button" onClick={() => props.onOpen(workspace.id)} disabled={busy}>Open workspace →</button>
        </article>)}
      </div><form className="workspace-form" onSubmit={event => { event.preventDefault(); void save() }}>
        <h3>{editing ? 'Workspace settings' : 'Create a workspace'}</h3>
        <fieldset disabled={busy}><label htmlFor="workspace-name">Workspace name</label>
          <input id="workspace-name" value={form.name} maxLength={120} required onChange={event => setForm({ ...form, name: event.target.value })} placeholder="A name for your work" />
          <label htmlFor="workspace-description">Description <span className="muted">(optional)</span></label>
          <textarea id="workspace-description" value={form.description} maxLength={2000} rows={3} onChange={event => setForm({ ...form, description: event.target.value })} placeholder="What belongs here?" />
          <div className="kind-heading"><span>Record types</span><button type="button" className="text-button" onClick={() => setForm({ ...form, kinds: [] })}>Include all</button></div>
          <p className="muted">{form.kinds.length ? 'Only the selected types appear in this workspace.' : 'All types are included, including types added later.'}</p>
          <div className="kind-options">{allKinds.map(value => <label key={value}><input type="checkbox" checked={form.kinds.includes(value)} onChange={event => setForm({ ...form, kinds: event.target.checked ? [...form.kinds, value] : form.kinds.filter(item => item !== value) })} />{value}</label>)}</div>
          <label htmlFor="workspace-kind">Add a record type</label>
          <div className="add-kind"><input id="workspace-kind" value={kind} onChange={event => setKind(event.target.value)} placeholder="e.g. partnership" pattern="[a-z][a-z0-9_]{0,63}" maxLength={64} />
            <button className="button" type="button" disabled={!/^[a-z][a-z0-9_]{0,63}$/.test(kind)} onClick={() => { setForm({ ...form, kinds: [...new Set([...form.kinds, kind])] }); setKind('') }}><Plus size={16} />Add type</button></div>
          <p className="muted">Use the same type identifier when importing records. Lowercase letters, numbers, and underscores.</p>
        </fieldset>
        {error && <p className="inline-error" role="alert">{error}</p>}{message && <p className="workspace-success" role="status">{message}</p>}
        <div className="workspace-form-actions">{editing && <button className="text-button workspace-remove" type="button" disabled={busy} onClick={() => setDeleting(!deleting)}><Trash2 size={15} />Remove workspace</button>}
          <button className="button primary" type="submit" disabled={busy || !form.name.trim()}>{busy ? <LoaderCircle size={17} className="spin" /> : <Save size={17} />}Save workspace</button></div>
        {deleting && <div className="remove-workspace"><p>Remove this saved collection? Its records, drafts, and outbox stay in the engine.</p><button className="button" type="button" disabled={busy} onClick={async () => {
          setBusy(true); setError(null)
          try { await workspaceRequest<void>(props.token, `/${editing}`, 'DELETE', undefined, props.baseUrl); select(); await props.onSaved(); setMessage('Workspace removed. Your records are unchanged.') }
          catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not remove workspace.') }
          finally { setBusy(false) }
        }}>Confirm removal</button><button className="text-button" type="button" onClick={() => setDeleting(false)} disabled={busy}>Keep workspace</button></div>}
      </form></div>
    </>}
  </div>
}
