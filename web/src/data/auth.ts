import { workflowRoot } from './workflow'

/** The HttpOnly cookie and an origin-scoped proof are both required for local access. */
export type ReviewAccess = string | { readonly mode: 'session' } | null
export const LOCAL_SESSION = { mode: 'session' } as const
export const SESSION_PROOF_STORAGE_KEY = 'respawned.local-session-proof.v1'

function sessionProof(): string | null {
  try { return window.localStorage.getItem(SESSION_PROOF_STORAGE_KEY) }
  catch { return null }
}

function clearSessionProof(): void {
  try { window.localStorage.removeItem(SESSION_PROOF_STORAGE_KEY) }
  catch { /* An inaccessible store cannot supply a proof to later requests. */ }
}

export function accessHeaders(access: ReviewAccess): Record<string, string> {
  if (typeof access === 'string') return access ? { Authorization: `Bearer ${access}` } : {}
  if (access?.mode !== 'session') return {}
  // Read at request time so another tab's exchange or logout takes effect here.
  const proof = sessionProof()
  return { 'X-Respawned-Request': '1', ...(proof ? { 'X-Respawned-Session-Proof': proof } : {}) }
}

export const isLocalSession = (access: ReviewAccess) => typeof access === 'object' && access?.mode === 'session'

let initialization: Promise<boolean> | undefined
export function restoreLocalSession(): Promise<boolean> {
  // React StrictMode can mount twice. Consume a launch capability only once.
  if (initialization) return initialization
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const secret = fragment.get('login')
  if (secret !== null) window.history.replaceState(null, '', window.location.pathname + window.location.search)
  initialization = (async () => {
    const root = await workflowRoot()
    const response = await fetch(`${root}/session`, secret === null ? { credentials: 'same-origin', redirect: 'error', signal: AbortSignal.timeout(20_000), headers: accessHeaders(LOCAL_SESSION) } : {
      method: 'POST', credentials: 'same-origin', redirect: 'error', signal: AbortSignal.timeout(20_000),
      headers: { 'Content-Type': 'application/json', 'X-Respawned-Request': '1' },
      body: JSON.stringify({ secret }),
    })
    if (response.status === 404 && secret === null) return false
    const payload = await response.json()
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Could not open the local session. Restart respawned ui for a new link.')
    if (secret !== null && payload.authenticated === true) {
      if (typeof payload.session_proof !== 'string' || !payload.session_proof) {
        clearSessionProof()
        throw new Error('This engine does not provide a protected browser session. Update Respawned and run respawned ui for a new link, or use a server access token.')
      }
      try { window.localStorage.setItem(SESSION_PROOF_STORAGE_KEY, payload.session_proof) }
      catch { throw new Error('Browser storage is unavailable. Allow storage for this engine and run respawned ui for a new link, or use a server access token.') }
    }
    if (payload.authenticated === true && !sessionProof()) {
      throw new Error('This browser session needs a new launch link. Run respawned ui to reconnect securely.')
    }
    return payload.authenticated === true
  })()
  return initialization
}

export async function endLocalSession(): Promise<void> {
  const root = await workflowRoot()
  const response = await fetch(`${root}/session`, {
    method: 'DELETE', credentials: 'same-origin', redirect: 'error', signal: AbortSignal.timeout(20_000), headers: accessHeaders(LOCAL_SESSION),
  })
  if (!response.ok && response.status !== 401) throw new Error('Could not lock the local session. Try again.')
  clearSessionProof()
  initialization = undefined
}
