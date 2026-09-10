// Keep these request-contract tests on a legacy engine; workflow.test.ts covers discovery.
vi.mock('./workflow', async importOriginal => ({ ...await importOriginal<typeof import('./workflow')>(), workflowRoot: async (baseUrl = '', signal?: AbortSignal) => { signal?.throwIfAborted(); return `${baseUrl.replace(/\/+$/, '')}/v1/ui` } }))
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LOCAL_SESSION } from './auth'
import { CONNECTIONS_STORAGE_KEY, engineRequestOptions, LOCAL_ENGINE, normalizeEngineUrl, readSavedConnections, saveConnections, verifyEngineAccess } from './connections'
import { importRecords, readBootstrap, readSetup, saveModel } from './setup'
import { workspaceRequest } from './workspaces'

afterEach(() => vi.unstubAllGlobals())

describe('engine connection boundaries', () => {
  it('normalizes HTTPS roots and preserves a reverse-proxy base path', () => {
    expect(normalizeEngineUrl('  https://ENGINE.example:443/team/respawned/// ')).toBe('https://engine.example/team/respawned')
    expect(normalizeEngineUrl('http://127.0.0.1:8001/')).toBe('http://127.0.0.1:8001')
    expect(normalizeEngineUrl('http://[::1]:8001/')).toBe('http://[::1]:8001')
    expect(normalizeEngineUrl('http://localhost:8001')).toBe('http://localhost:8001')
  })

  it.each([
    'http://engine.example', 'http://192.168.1.20:8000', 'http://127.0.0.1:0', 'https://engine.example:0', 'file:///tmp/engine',
    'https://operator:secret@engine.example', 'https://engine.example?token=secret',
    'https://engine.example#login=secret', 'engine.example',
  ])('rejects insecure or credential-bearing engine address %s', value => {
    expect(() => normalizeEngineUrl(value)).toThrow()
  })

  it('never forwards local sessions or browser cookies to remote engines', () => {
    expect(engineRequestOptions(LOCAL_SESSION)).toEqual({ credentials: 'same-origin', redirect: 'error' })
    expect(engineRequestOptions('remote-secret', 'https://remote.example')).toEqual({ credentials: 'omit', redirect: 'error' })
    expect(() => engineRequestOptions(LOCAL_SESSION, 'https://remote.example')).toThrow('cannot be forwarded')
  })

  it('persists connection metadata only and sanitizes malformed saved entries', () => {
    const storage = { getItem: vi.fn(), setItem: vi.fn() }
    const remote = { id: 'remote-one', name: 'Project server', baseUrl: 'https://remote.example', token: 'never-store-this', access: LOCAL_SESSION }
    saveConnections([LOCAL_ENGINE, remote], storage)
    expect(storage.setItem).toHaveBeenCalledWith(CONNECTIONS_STORAGE_KEY, JSON.stringify([{ id: 'remote-one', name: 'Project server', baseUrl: 'https://remote.example' }]))
    storage.getItem.mockReturnValue(JSON.stringify([
      remote, { ...remote, id: 'duplicate', baseUrl: 'https://remote.example/' },
      { ...remote, id: 'unsafe', baseUrl: 'https://user:secret@unsafe.example' },
      { id: 'local', name: 'Override local engine', baseUrl: 'https://override.example' },
      { id: 'broken', name: 42, baseUrl: 'https://other.example' },
    ]))
    expect(readSavedConnections(storage)).toEqual([LOCAL_ENGINE, { id: 'remote-one', name: 'Project server', baseUrl: 'https://remote.example' }])
    storage.getItem.mockReturnValue('{broken')
    expect(readSavedConnections(storage)).toEqual([LOCAL_ENGINE])
  })

  it('checks remote access before accepting a connection, without allowing redirects', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ review: { enabled: true }, database: { status: 'ready' } })))
    vi.stubGlobal('fetch', fetch)
    await verifyEngineAccess('https://remote.example/respawned', ' remote-token ')
    expect(fetch).toHaveBeenCalledWith('https://remote.example/respawned/v1/ui/setup', expect.objectContaining({
      credentials: 'omit', redirect: 'error', headers: { Accept: 'application/json', Authorization: 'Bearer remote-token' },
    }))
  })

  it('reports rejected access and unreachable servers without echoing tokens', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response('{"detail":"private-token"}', { status: 401 })).mockRejectedValueOnce(new Error('private-token'))
    vi.stubGlobal('fetch', fetch)
    await expect(verifyEngineAccess('https://remote.example', 'private-token')).rejects.toThrow('did not accept the token')
    await expect(verifyEngineAccess('https://remote.example', 'private-token')).rejects.toThrow('RESPAWNED_UI_ORIGINS')
  })

  it('routes setup, import, model, and workspace operations only to the selected engine', async () => {
    const fetch = vi.fn().mockImplementation(async (url: string) => new Response(url.endsWith('/bootstrap') ? '{"review_enabled":true}' : '{}'))
    vi.stubGlobal('fetch', fetch)
    const baseUrl = 'https://remote.example/team'
    await readSetup('remote-only', undefined, baseUrl)
    await readBootstrap(undefined, baseUrl)
    await saveModel('remote-only', { backend: 'codex_cli', base_url: '', model_alias: '', timeout_seconds: 120, api_key_env: 'LITELLM_MASTER_KEY' }, baseUrl)
    await importRecords('remote-only', { opportunities: [{ id: 'remote-record' }], activities: [] }, baseUrl)
    await workspaceRequest('remote-only', '/remote-workspace', 'PUT', { name: 'Remote work', description: '', kinds: [] }, baseUrl)
    expect(fetch.mock.calls.map(call => call[0])).toEqual([
      `${baseUrl}/v1/ui/setup`, `${baseUrl}/v1/setup/bootstrap`, `${baseUrl}/v1/ui/setup/model`, `${baseUrl}/v1/ui/import`, `${baseUrl}/v1/ui/workspaces/remote-workspace`,
    ])
    for (const [, options] of fetch.mock.calls) {
      expect(options).toMatchObject({ credentials: 'omit', redirect: 'error' })
    }
    expect(fetch.mock.calls[1][1].headers).not.toHaveProperty('Authorization')
    expect(fetch.mock.calls[3][1]).toMatchObject({ method: 'POST', headers: { Authorization: 'Bearer remote-only' } })
    expect(fetch.mock.calls[4][1]).toMatchObject({ method: 'PUT', headers: { Authorization: 'Bearer remote-only' } })
    await expect(readSetup(LOCAL_SESSION, undefined, baseUrl)).rejects.toThrow()
    expect(fetch).toHaveBeenCalledTimes(5)
  })
})
