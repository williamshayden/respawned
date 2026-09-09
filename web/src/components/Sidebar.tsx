import { BarChart3, ChevronDown, NotebookText, Mail, RefreshCw, Send, ShieldCheck, X } from 'lucide-react'
import { contextNames, type Page } from '../presentation'

const destinations = [
  { name: 'Review queue', icon: NotebookText }, { name: 'Reply inbox', icon: Mail },
  { name: 'Outbox', icon: Send }, { name: 'Activity', icon: BarChart3 }, { name: 'Policy', icon: ShieldCheck },
] as const

interface Props {
  page: Page; onNavigate: (page: Page) => void
  scope: string; scopes: string[]; onScope: (scope: string) => void
  counts: Partial<Record<Page, number>>; mode: 'demo' | 'live'
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
        <label htmlFor="context-scope">Workspace</label>
        <div className="select-wrap">
          <select id="context-scope" value={props.scope} onChange={event => props.onScope(event.target.value)}>
            <option value="all">All work</option>
            {props.scopes.map(kind => <option key={kind} value={kind}>{contextNames[kind] || kind.replaceAll('_', ' ')}</option>)}
          </select><ChevronDown size={16} aria-hidden="true" />
        </div>
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
        <button className="workspace-status" onClick={props.onConnect}>
          <span className="status-dot" /><span>{props.mode === 'demo' ? 'Demo workspace' : 'Local workspace'}</span>
        </button>
        <button className="profile" onClick={props.onConnect} aria-label="Workspace connection settings">
          <span className="avatar">{props.mode === 'demo' ? 'W' : 'LO'}</span>
          <span>{props.mode === 'demo' ? 'William' : 'Local operator'}</span><ChevronDown size={17} />
        </button>
      </div>
    </aside>
  </>
}
