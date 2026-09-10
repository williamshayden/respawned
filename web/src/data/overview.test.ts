// Keep these request-contract tests on a legacy engine; workflow.test.ts covers discovery.
vi.mock('./workflow', async importOriginal => ({ ...await importOriginal<typeof import('./workflow')>(), workflowRoot: async (baseUrl = '', signal?: AbortSignal) => { signal?.throwIfAborted(); return `${baseUrl.replace(/\/+$/, '')}/v1/ui` } }))
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LOCAL_SESSION } from './auth'
import { isWatched, readOverview, readWatchPreferences, watchKey, type Overview } from './overview'

afterEach(() => vi.unstubAllGlobals())
const summary: Overview = { generated_at: '2026-09-09T18:00:00Z', source_freshness: 'unknown', workspaces: [
  { id: 'all', name: 'All work', description: '', kinds: [], counts: { records: 503, ready: 280, pending_drafts: 12, reply_contacts: 8, pending_outbox: 2 } },
] }

describe('engine overview boundary', () => {
  it('uses the server totals and remote bearer access without forwarding cookies or redirects', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(summary)))
    vi.stubGlobal('fetch', fetch)
    expect(await readOverview('https://remote.example.com/app', 'remote-secret')).toEqual(summary)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('https://remote.example.com/app/v1/ui/overview', {
      credentials: 'omit', redirect: 'error', headers: { Accept: 'application/json', Authorization: 'Bearer remote-secret' }, signal: undefined,
    })
  })
  it('uses only the local session header and same-origin cookie for the current engine', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(summary)))
    vi.stubGlobal('fetch', fetch)
    await readOverview('', LOCAL_SESSION)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/ui/overview', {
      credentials: 'same-origin', redirect: 'error', headers: { Accept: 'application/json', 'X-Respawned-Request': '1' }, signal: undefined,
    })
  })
  it('does not request a locked engine or forward a local session to a remote destination', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(readOverview('', null)).rejects.toMatchObject({ kind: 'access' })
    await expect(readOverview('https://remote.example.com', LOCAL_SESSION)).rejects.toThrow()
    expect(fetch).not.toHaveBeenCalled()
  })
  it.each([[401, 'access'], [403, 'access'], [404, 'unsupported'], [503, 'offline']])('preserves HTTP %s as %s instead of manufacturing empty counts', async (status, kind) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: Number(status) })))
    await expect(readOverview('', 'secret')).rejects.toMatchObject({ kind })
  })
  it('rejects malformed counts and duplicate workspace identities', async () => {
    const invalid = structuredClone(summary)
    invalid.workspaces[0].counts.ready = -1
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(invalid)))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...summary, workspaces: [...summary.workspaces, ...summary.workspaces] })))
    vi.stubGlobal('fetch', fetch)
    await expect(readOverview('', 'secret')).rejects.toMatchObject({ kind: 'invalid' })
    await expect(readOverview('', 'secret')).rejects.toMatchObject({ kind: 'invalid' })
  })
  it('allows callers to cancel outdated requests', async () => {
    const controller = new AbortController()
    const error = new DOMException('Aborted', 'AbortError')
    controller.abort(error)
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(error))
    await expect(readOverview('', 'secret', controller.signal)).rejects.toBe(error)
  })
})

describe('workspace watch preferences', () => {
  it('defaults to all work per engine and keeps identical remote workspace IDs distinct', () => {
    expect(isWatched({}, 'local', 'all')).toBe(true)
    expect(isWatched({}, 'remote', 'all')).toBe(true)
    expect(isWatched({}, 'remote', 'partnerships')).toBe(false)
    const selected = { [watchKey('local', 'all')]: false, [watchKey('remote', 'partnerships')]: true }
    expect(isWatched(selected, 'local', 'all')).toBe(false)
    expect(isWatched(selected, 'remote', 'all')).toBe(true)
    expect(isWatched(selected, 'remote', 'partnerships')).toBe(true)
    expect(isWatched(selected, 'local', 'partnerships')).toBe(false)
    expect(watchKey('a:b', 'c')).not.toBe(watchKey('a', 'b:c'))
  })
  it('restores only explicit identity and boolean entries from browser preferences', () => {
    const key = watchKey('local', 'all')
    expect(readWatchPreferences(JSON.stringify({ [key]: false, invalid: true, '["one"]': true, '["a","b"]': 'token', '[1,"b"]': true }))).toEqual({ [key]: false })
    expect(readWatchPreferences('broken')).toEqual({})
    expect(readWatchPreferences('[]')).toEqual({})
  })
})
