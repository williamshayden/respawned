// Keep these request-contract tests on a legacy engine; workflow.test.ts covers discovery.
vi.mock('./workflow', async importOriginal => ({ ...await importOriginal<typeof import('./workflow')>(), workflowRoot: async (baseUrl = '', signal?: AbortSignal) => { signal?.throwIfAborted(); return `${baseUrl.replace(/\/+$/, '')}/v1/ui` } }))
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => vi.resetModules())
afterEach(() => vi.unstubAllGlobals())

function browser(hash = '') {
  const replaceState = vi.fn()
  const values = new Map<string, string>()
  vi.stubGlobal('window', { location: { hash, pathname: '/', search: '', origin: 'http://127.0.0.1:8000' }, history: { replaceState }, localStorage: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  } })
  return replaceState
}

describe('local CLI access', () => {
  it('exchanges a fragment once, removes it immediately, and never uses a fake bearer token', async () => {
    const history = browser('#login=one-use-launch-secret')
    const fetch = vi.fn().mockResolvedValue(new Response('{"authenticated":true,"session_proof":"origin-proof"}'))
    vi.stubGlobal('fetch', fetch)
    const auth = await import('./auth')
    const first = auth.restoreLocalSession()
    expect(history).toHaveBeenCalledWith(null, '', '/')
    const second = auth.restoreLocalSession()
    expect(await first).toBe(true)
    expect(await second).toBe(true)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/ui/session', {
      method: 'POST', credentials: 'same-origin', redirect: 'error', signal: expect.any(AbortSignal),
      headers: { 'Content-Type': 'application/json', 'X-Respawned-Request': '1' },
      body: '{"secret":"one-use-launch-secret"}',
    })
    expect(auth.accessHeaders(auth.LOCAL_SESSION)).toEqual({ 'X-Respawned-Request': '1', 'X-Respawned-Session-Proof': 'origin-proof' })
    expect(auth.accessHeaders('api-secret')).toEqual({ Authorization: 'Bearer api-secret' })
    expect(window.localStorage.getItem(auth.SESSION_PROOF_STORAGE_KEY)).toBe('origin-proof')
  })

  it('restores a cookie connection with the origin proof after reload', async () => {
    browser()
    const fetch = vi.fn().mockResolvedValue(new Response('{"authenticated":true}'))
    vi.stubGlobal('fetch', fetch)
    const auth = await import('./auth')
    window.localStorage.setItem(auth.SESSION_PROOF_STORAGE_KEY, 'persisted-proof')
    expect(await auth.restoreLocalSession()).toBe(true)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/ui/session', { credentials: 'same-origin', redirect: 'error', signal: expect.any(AbortSignal), headers: { 'X-Respawned-Request': '1', 'X-Respawned-Session-Proof': 'persisted-proof' } })
    // A fresh launch in another tab rotates the shared cookie and proof together.
    window.localStorage.setItem(auth.SESSION_PROOF_STORAGE_KEY, 'another-tab-proof')
    expect(auth.accessHeaders(auth.LOCAL_SESSION)['X-Respawned-Session-Proof']).toBe('another-tab-proof')
  })

  it('reports an expired launch link and revokes the cookie on lock', async () => {
    browser('#login=expired')
    const fetch = vi.fn().mockResolvedValueOnce(new Response('{"detail":"Launch link expired"}', { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetch)
    const auth = await import('./auth')
    window.localStorage.setItem(auth.SESSION_PROOF_STORAGE_KEY, 'active-proof')
    await expect(auth.restoreLocalSession()).rejects.toThrow('Launch link expired')
    await auth.endLocalSession()
    expect(fetch).toHaveBeenLastCalledWith('/v1/ui/session', {
      method: 'DELETE', credentials: 'same-origin', redirect: 'error', signal: expect.any(AbortSignal), headers: { 'X-Respawned-Request': '1', 'X-Respawned-Session-Proof': 'active-proof' },
    })
    expect(window.localStorage.getItem(auth.SESSION_PROOF_STORAGE_KEY)).toBeNull()
    expect(auth.accessHeaders(auth.LOCAL_SESSION)).toEqual({ 'X-Respawned-Request': '1' })
  })

  it('does not unlock an old engine that exchanges a cookie without a proof', async () => {
    browser('#login=launch')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"authenticated":true}')))
    const auth = await import('./auth')
    await expect(auth.restoreLocalSession()).rejects.toThrow('does not provide a protected browser session')
    expect(window.localStorage.getItem(auth.SESSION_PROOF_STORAGE_KEY)).toBeNull()
  })

  it('does not treat a cookie-only restoration as authenticated', async () => {
    browser()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"authenticated":true}')))
    const auth = await import('./auth')
    await expect(auth.restoreLocalSession()).rejects.toThrow('needs a new launch link')
  })

  it('fails clearly when browser storage cannot preserve an exchanged proof', async () => {
    browser('#login=launch')
    window.localStorage.setItem = () => { throw new Error('Storage denied') }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"authenticated":true,"session_proof":"origin-proof"}')))
    const auth = await import('./auth')
    await expect(auth.restoreLocalSession()).rejects.toThrow('Browser storage is unavailable')
  })

  it('sends the proof on local reads, writes, setup, workspaces, and monitoring but never bearer requests', async () => {
    browser()
    const auth = await import('./auth')
    window.localStorage.setItem(auth.SESSION_PROOF_STORAGE_KEY, 'private-origin-proof')
    const fetch = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith('/overview')
      ? { generated_at: '2026-09-09T18:00:00Z', source_freshness: 'unknown', workspaces: [] } : {})))
    vi.stubGlobal('fetch', fetch)
    const { createHttpClient } = await import('./client')
    const { readSetup, importRecords } = await import('./setup')
    const { workspaceRequest } = await import('./workspaces')
    const { readOverview } = await import('./overview')
    const local = createHttpClient('', auth.LOCAL_SESSION)
    await Promise.all([
      local.config(), local.edit('draft-id', 'Reviewed body', 'review-token'),
      readSetup(auth.LOCAL_SESSION), importRecords(auth.LOCAL_SESSION, { opportunities: [], activities: [] }),
      workspaceRequest(auth.LOCAL_SESSION), readOverview('', auth.LOCAL_SESSION),
    ])
    for (const [url, options] of fetch.mock.calls as unknown as [string, RequestInit][]) {
      expect(url).not.toContain('private-origin-proof')
      expect(options.credentials).toBe('same-origin')
      expect(options.headers).toMatchObject({ 'X-Respawned-Session-Proof': 'private-origin-proof' })
    }
    fetch.mockClear()
    await createHttpClient('https://remote.example', 'remote-bearer').config()
    expect(fetch).toHaveBeenCalledExactlyOnceWith('https://remote.example/v1/ui/config', expect.objectContaining({
      credentials: 'omit', headers: { Accept: 'application/json', Authorization: 'Bearer remote-bearer' },
    }))
    expect(() => createHttpClient('https://remote.example', auth.LOCAL_SESSION)).toThrow('cannot be forwarded')
  })
})
