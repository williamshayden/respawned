import { useState } from 'react'
import { ArrowUpRight, Check, Globe2, Link2, LoaderCircle, LockKeyhole, Plus, Server, Trash2 } from 'lucide-react'
import type { ReviewAccess } from '../data/auth'
import type { EngineConnection } from '../data/connections'
import './connections.css'

interface Props {
  connections: EngineConnection[]
  accessById: Record<string, ReviewAccess>
  activeId: string
  onAdd(name: string, url: string, token: string): Promise<unknown>
  onUnlock(id: string, token: string): Promise<void>
  onLock(id: string): Promise<void> | void
  onRemove(id: string): Promise<void> | void
  onOpen(id: string): void
}

export function ConnectionsView({ connections, accessById, activeId, onAdd, onUnlock, onLock, onRemove, onOpen }: Props) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [unlockingId, setUnlockingId] = useState<string | null>(null)
  const [unlockToken, setUnlockToken] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const browserOrigin = window.location.origin
  const perform = async (operation: string, action: () => Promise<void>) => {
    setBusy(operation); setError(null); setNotice(null)
    try { await action() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not update this connection.') }
    finally { setBusy(null) }
  }

  return <div className="connections-view">
    <p className="connections-intro">Connect another Respawned server to monitor its workspaces. Each engine keeps its own records, settings, and approvals.</p>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {notice && <p className="connections-success" role="status"><Check size={16} />{notice}</p>}
    <div className="connections-grid"><section className="connection-list" aria-label="Saved engines">
      {connections.map(connection => {
        const unlocked = Boolean(accessById[connection.id])
        const local = connection.id === 'local'
        return <article className={`connection-card ${connection.id === activeId ? 'is-active' : ''}`} key={connection.id}>
          <div className="connection-card-heading">{local ? <Server size={20} /> : <Globe2 size={20} />}<div><h3>{connection.name}</h3><p>{connection.baseUrl || browserOrigin}</p></div><span className={`connection-access ${unlocked ? 'is-ready' : ''}`}>{unlocked ? 'Unlocked' : 'Locked'}</span></div>
          <div className="connection-card-actions"><button className="button small" disabled={Boolean(busy)} onClick={() => onOpen(connection.id)}>Open engine<ArrowUpRight size={14} /></button>
            {unlocked ? <button className="text-button" disabled={Boolean(busy)} onClick={() => void perform(connection.id, async () => { await onLock(connection.id); setNotice(`${connection.name} is locked.`) })}><LockKeyhole size={14} />Lock</button> : !local && <button className="text-button" disabled={Boolean(busy)} onClick={() => { setUnlockingId(connection.id); setUnlockToken(''); setError(null) }}>Unlock</button>}
            {!local && <button className="text-button connection-remove" disabled={Boolean(busy)} aria-label={`Remove connection to ${connection.name}`} onClick={() => void perform(connection.id, async () => { await onRemove(connection.id); if (unlockingId === connection.id) { setUnlockingId(null); setUnlockToken('') } setNotice('Connection removed from this browser. The engine and its records are unchanged.') })}><Trash2 size={14} />Remove</button>}
          </div>
          {local && !unlocked && <p className="connection-help">Open this engine to reconnect from Setup or use the link from <code>respawned ui</code>.</p>}
          {!unlocked && unlockingId === connection.id && <form className="connection-unlock" onSubmit={event => {
            event.preventDefault()
            void perform(connection.id, async () => { await onUnlock(connection.id, unlockToken); setUnlockToken(''); setUnlockingId(null); setNotice(`${connection.name} is unlocked for this tab.`) })
          }}><label htmlFor={`unlock-${connection.id}`}>Access token for {connection.name}</label><div><input id={`unlock-${connection.id}`} type="password" autoComplete="off" spellCheck={false} value={unlockToken} onChange={event => setUnlockToken(event.target.value)} required disabled={Boolean(busy)} /><button className="button primary small" disabled={Boolean(busy) || !unlockToken.trim()}>{busy === connection.id && <LoaderCircle size={15} className="spin" />}Connect</button></div></form>}
        </article>
      })}
      <p className="connection-help">Names and URLs are remembered in this browser when storage is available. Remote access tokens stay in this tab’s memory and clear on reload. Removing a connection does not delete anything on its server.</p>
    </section><section className="connection-add" aria-labelledby="connection-add-title"><h3 id="connection-add-title"><Plus size={18} />Connect an engine</h3><p>Enter the server’s root URL and review access token.</p>
      <form onSubmit={event => {
        event.preventDefault()
        void perform('add', async () => { await onAdd(name, url, token); setName(''); setUrl(''); setToken(''); setNotice('Engine connected. Open Overview to choose the workspaces you want to monitor.') })
      }}><fieldset disabled={Boolean(busy)}><label htmlFor="connection-name">Engine name</label><input id="connection-name" value={name} onChange={event => setName(event.target.value)} maxLength={120} placeholder="A name you’ll recognize" required />
        <label htmlFor="connection-url">Engine URL</label><input id="connection-url" type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://respawned.example.com" autoComplete="url" spellCheck={false} required /><span className="connection-help">HTTPS is required remotely. Another local engine can use http://127.0.0.1:8001. Include a reverse-proxy path if your server uses one.</span>
        <label htmlFor="connection-token">Engine access token</label><input id="connection-token" type="password" autoComplete="off" spellCheck={false} value={token} onChange={event => setToken(event.target.value)} required /><span className="connection-help">Use this engine’s RESPAWNED_REVIEW_TOKEN. Your local browser session is never sent to another engine.</span>
        <button className="button primary" type="submit" disabled={!name.trim() || !url.trim() || !token.trim()}>{busy === 'add' ? <LoaderCircle size={16} className="spin" /> : <Link2 size={16} />}Connect engine</button></fieldset></form>
      <details className="connection-server-help"><summary>Prepare a remote engine</summary><p>Set a review token on the remote server and serve it over HTTPS. Allow this UI’s exact origin in its server environment, then restart Respawned:</p><pre><code>RESPAWNED_UI_ORIGINS={browserOrigin}</code></pre><p>Multiple allowed origins are comma-separated. Use the browser origin above without a trailing slash or path. The browser connects directly to that API; this app does not relay credentials through the local engine.</p></details>
    </section></div>
  </div>
}
