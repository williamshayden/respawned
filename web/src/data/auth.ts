import { workflowRoot } from './workflow'

/** A browser session is held in an HttpOnly cookie, never in JS storage. */
export type ReviewAccess = string | { readonly mode: 'session' } | null
export const LOCAL_SESSION = { mode: 'session' } as const

export function accessHeaders(access: ReviewAccess): Record<string, string> {
  if (typeof access === 'string') return access ? { Authorization: `Bearer ${access}` } : {}
  return access?.mode === 'session' ? { 'X-Respawned-Request': '1' } : {}
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
    const response = await fetch(`${root}/session`, secret === null ? { credentials: 'same-origin', redirect: 'error', signal: AbortSignal.timeout(20_000) } : {
      method: 'POST', credentials: 'same-origin', redirect: 'error', signal: AbortSignal.timeout(20_000),
      headers: { 'Content-Type': 'application/json', 'X-Respawned-Request': '1' },
      body: JSON.stringify({ secret }),
    })
    if (response.status === 404 && secret === null) return false
    const payload = await response.json()
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Could not open the local session. Restart respawned ui for a new link.')
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
  initialization = undefined
}
