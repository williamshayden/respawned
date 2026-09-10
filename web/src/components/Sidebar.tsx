import { BarChart3, ChevronDown, FolderCog, LayoutDashboard, Network, NotebookText, Mail, RefreshCw, Send, Settings2, ShieldCheck, X } from 'lucide-react'
import { type Page } from '../presentation'
import type { Workspace } from '../data/workspaces'
import type { EngineConnection } from '../data/connections'

const destinations = [
  { name: 'Overview', icon: LayoutDashboard }, { name: 'Connections', icon: Network },
  { name: 'Review queue', icon: NotebookText }, { name: 'Reply inbox', icon: Mail },
  { name: 'Outbox', icon: Send }, { name: 'Activity', icon: BarChart3 }, { name: 'Policy', icon: ShieldCheck },
  { name: 'Setup', icon: Settings2 },
] as const

interface Props {
  page: Page; onNavigate: (page: Page) => void
  scope: string; workspaces: Workspace[]; onScope: (scope: string) => void
  connections: EngineConnection[]; activeConnectionId: string; onConnection: (id: string) => void
  counts: Partial<Record<Page, number>>; connected: boolean
  onConnect: () => void; open: boolean; onClose: () => void
}

export function Sidebar(props: Props) {
  return <>
    {props.open && <button className="sidebar-scrim" aria-label="Close navigation" onClick={props.onClose} />}
    <aside className={`sidebar ${props.open ? 'is-open' : ''}`}>
      <a className="brand" href="#" onClick={event => { event.preventDefault(); props.onNavigate('Review queue') }}>
        <RefreshCw className="brand-mark" aria-hidden="true" />
        <span>Respawned</span>
      </a>
      <button className="icon-button mobile-close" aria-label="Close navigation" onClick={props.onClose}><X /></button>
      <div className="workspace-selector">
        <label htmlFor="engine-connection">Engine</label>
        <div className="select-wrap">
          <select id="engine-connection" value={props.activeConnectionId} onChange={event => props.onConnection(event.target.value)}>
            {props.connections.map(connection => <option key={connection.id} value={connection.id}>{connection.name}</option>)}
          </select><ChevronDown size={16} aria-hidden="true" />
        </div>
        <label htmlFor="context-scope">Workspace</label>
        <div className="select-wrap">
          <select id="context-scope" value={props.scope} onChange={event => props.onScope(event.target.value)}>
            <option value="all">All work</option>
            {props.workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
          </select><ChevronDown size={16} aria-hidden="true" />
        </div>
        <button className="text-button manage-workspaces" aria-current={props.page === 'Workspaces' ? 'page' : undefined} onClick={() => props.onNavigate('Workspaces')}><FolderCog size={16} />Manage workspaces</button>
      </div>
      <nav aria-label="Main navigation">
        {destinations.map(({ name, icon: Icon }) => <button key={name}
          className={`nav-item ${props.page === name ? 'is-active' : ''}`}
          aria-current={props.page === name ? 'page' : undefined}
          onClick={() => props.onNavigate(name)}>
          <Icon size={23} strokeWidth={1.7} aria-hidden="true" /><span>{name}</span>
          {props.counts[name] != null && <span className="count">{props.counts[name]}</span>}
        </button>)}
      </nav>
      <div className="sidebar-bottom">
        <button className={`workspace-status ${props.connected ? '' : 'is-disconnected'}`} onClick={props.onConnect}>
          <span className="status-dot" /><span>{props.connected ? 'Engine connected' : 'Setup required'}</span>
        </button>
      </div>
    </aside>
  </>
}
