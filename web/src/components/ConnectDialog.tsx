import { useEffect, useRef, useState } from 'react'
import { LoaderCircle, X } from 'lucide-react'

interface Props { mode: 'demo' | 'live'; onClose: () => void; onConnect: (token: string) => Promise<void>; onDemo: () => void }
export function ConnectDialog(props: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close() }, [])
  return <dialog ref={dialog} className="connection-dialog" onCancel={event => { if (busy) event.preventDefault(); else props.onClose() }} aria-labelledby="connection-heading">
    <div className="dialog-header"><h2 id="connection-heading">Workspace connection</h2><button className="icon-button" onClick={props.onClose} disabled={busy} aria-label="Close settings"><X size={21} /></button></div>
    <p>Explore fictional records in the demo, or connect to your local engine.</p>
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError(null)
      try { await props.onConnect(token); props.onClose() }
      catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not connect.') }
      finally { setBusy(false) }
    }}>
      <label htmlFor="review-token">Review access token</label>
      <input id="review-token" autoComplete="off" type="password" value={token} onChange={event => setToken(event.target.value)} placeholder="Enter your local review token" required />
      <p className="muted">Use the token configured with RESPAWNED_REVIEW_TOKEN on your engine. It is kept in memory for this session.</p>
      {error && <p className="inline-error" role="alert">{error}</p>}
      <div className="dialog-actions"><button type="button" className="button" onClick={() => { props.onDemo(); props.onClose() }} disabled={busy}>Use demo</button>
        <button className="button primary" type="submit" disabled={busy || !token.trim()}>{busy && <LoaderCircle size={16} className="spin" />}Connect local engine</button></div>
    </form>
  </dialog>
}
