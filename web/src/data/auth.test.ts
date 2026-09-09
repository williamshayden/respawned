import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => vi.resetModules())
afterEach(() => vi.unstubAllGlobals())

function browser(hash = '') {
  const replaceState = vi.fn()
  vi.stubGlobal('window', { location: { hash, pathname: '/', search: '' }, history: { replaceState } })
  return replaceState
}

describe('local CLI access', () => {
  it('exchanges a fragment once, removes it immediately, and never uses a fake bearer token', async () => {
    const history = browser('#login=one-use-launch-secret')
    const fetch = vi.fn().mockResolvedValue(new Response('{"authenticated":true}'))
    vi.stubGlobal('fetch', fetch)
    const auth = await import('./auth')
    const first = auth.restoreLocalSession()
    expect(history).toHaveBeenCalledWith(null, '', '/')
    const second = auth.restoreLocalSession()
    expect(await first).toBe(true)
    expect(await second).toBe(true)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/ui/session', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Respawned-Request': '1' },
      body: '{"secret":"one-use-launch-secret"}',
    })
    expect(auth.accessHeaders(auth.LOCAL_SESSION)).toEqual({ 'X-Respawned-Request': '1' })
    expect(auth.accessHeaders('api-secret')).toEqual({ Authorization: 'Bearer api-secret' })
  })

  it('restores a cookie connection after reload without exposing its secret to JavaScript', async () => {
    browser()
    const fetch = vi.fn().mockResolvedValue(new Response('{"authenticated":true}'))
    vi.stubGlobal('fetch', fetch)
    const auth = await import('./auth')
    expect(await auth.restoreLocalSession()).toBe(true)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/ui/session', { credentials: 'same-origin' })
  })

  it('reports an expired launch link and revokes the cookie on lock', async () => {
    browser('#login=expired')
    const fetch = vi.fn().mockResolvedValueOnce(new Response('{"detail":"Launch link expired"}', { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetch)
    const auth = await import('./auth')
    await expect(auth.restoreLocalSession()).rejects.toThrow('Launch link expired')
    await auth.endLocalSession()
    expect(fetch).toHaveBeenLastCalledWith('/v1/ui/session', {
      method: 'DELETE', credentials: 'same-origin', headers: { 'X-Respawned-Request': '1' },
    })
  })
})
