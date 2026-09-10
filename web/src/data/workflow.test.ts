import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => vi.resetModules())
afterEach(() => vi.unstubAllGlobals())

function backend(prefix: unknown = '/v1/workflow') {
  const fetch = vi.fn(async (url: string) => {
    if (url.endsWith('/bootstrap')) return new Response(JSON.stringify({ review_enabled: true, ...(prefix === 'absent' ? {} : { workflow_api_prefix: prefix }) }))
    if (url.endsWith('/overview')) return new Response(JSON.stringify({ generated_at: '2026-09-10T12:00:00Z', source_freshness: 'unknown', workspaces: [] }))
    if (url.endsWith('/setup')) return new Response(JSON.stringify({ review: { enabled: true }, database: { status: 'ready' } }))
    if (url.includes('/outbox?')) return new Response('{"items":[],"has_more":false}')
    if (url.endsWith('/session')) return new Response('{"authenticated":true}')
    return new Response('{}')
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('workflow API discovery', () => {
  it('negotiates once and shares the canonical root across review, setup, workspaces and overview', async () => {
    const fetch = backend()
    const { createHttpClient } = await import('./client')
    const { readSetup, saveModel, importRecords } = await import('./setup')
    const { workspaceRequest } = await import('./workspaces')
    const { readOverview } = await import('./overview')
    const { verifyEngineAccess } = await import('./connections')
    const base = 'https://engine.example/team'
    const client = createHttpClient(base + '/', 'engine-token')
    await Promise.all([
      client.config(), client.listRecords(), client.listOutbox(), client.listInbox(),
      readSetup('engine-token', undefined, base), workspaceRequest('engine-token', '', 'GET', undefined, base),
      readOverview(base, 'engine-token'), verifyEngineAccess(base, 'engine-token'),
    ])
    await client.approve('draft/1', 'review-version')
    await importRecords('engine-token', { opportunities: [{ id: 'record-1' }], activities: [] }, base)
    await saveModel('engine-token', { backend: 'codex_cli', base_url: '', model_alias: '', api_key_env: 'LITELLM_MASTER_KEY', timeout_seconds: 120 }, base)
    expect(fetch.mock.calls.filter(([url]) => url.endsWith('/bootstrap'))).toHaveLength(1)
    const requests = fetch.mock.calls as unknown as [string, RequestInit][]
    expect(requests[0]).toEqual([`${base}/v1/setup/bootstrap`, expect.objectContaining({
      credentials: 'omit', redirect: 'error', headers: { Accept: 'application/json' },
    })])
    for (const [url, options] of requests.slice(1)) {
      expect(url).toContain(`${base}/v1/workflow/`)
      expect(options).toMatchObject({ credentials: 'omit', redirect: 'error', headers: { Authorization: 'Bearer engine-token' } })
    }
    expect(requests.find(([url]) => url.endsWith('/approve'))?.[1].body).toBe('{"review_token":"review-version"}')
  })

  it.each([undefined, null, '/v1/workflow/', '//other.example/v1/workflow', 'https://other.example/v1/workflow'])('uses legacy routes when bootstrap does not advertise the exact supported path: %s', async prefix => {
    const fetch = backend(prefix === undefined ? 'absent' : prefix)
    const { createHttpClient } = await import('./client')
    await createHttpClient('https://legacy.example', 'legacy-token').approve('draft', 'version')
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      'https://legacy.example/v1/setup/bootstrap', 'https://legacy.example/v1/ui/drafts/draft/approve',
    ])
  })

  it('keeps each engine decision and bearer credential separate', async () => {
    const fetch = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith('/bootstrap')
      ? { review_enabled: true, ...(url.includes('new.example') ? { workflow_api_prefix: '/v1/workflow' } : {}) } : {})))
    vi.stubGlobal('fetch', fetch)
    const { createHttpClient } = await import('./client')
    await createHttpClient('https://new.example', 'new-token').approve('same-id', 'new-version')
    await createHttpClient('https://old.example', 'old-token').approve('same-id', 'old-version')
    const writes = (fetch.mock.calls as unknown as [string, RequestInit][]).filter(([, options]) => options.method === 'POST')
    expect(writes).toEqual([
      ['https://new.example/v1/workflow/drafts/same-id/approve', expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer new-token' }) })],
      ['https://old.example/v1/ui/drafts/same-id/approve', expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer old-token' }) })],
    ])
  })

  it.each([401, 404, 409, 503])('never retries a failed canonical mutation against legacy routes (HTTP %s)', async status => {
    const fetch = vi.fn(async (url: string) => url.endsWith('/bootstrap')
      ? new Response('{"review_enabled":true,"workflow_api_prefix":"/v1/workflow"}')
      : new Response('{"detail":"Operation did not complete"}', { status }))
    vi.stubGlobal('fetch', fetch)
    const { createHttpClient } = await import('./client')
    await expect(createHttpClient('', 'operator').approve('draft', 'version')).rejects.toMatchObject({ status })
    expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/v1/setup/bootstrap', '/v1/workflow/drafts/draft/approve'])
  })

  it('does not send credentials or a write when discovery fails and lets an explicit retry rediscover', async () => {
    const fetch = vi.fn(async () => new Response('{}', { status: 503 }))
    vi.stubGlobal('fetch', fetch)
    const { createHttpClient } = await import('./client')
    const client = createHttpClient('', 'operator')
    await expect(client.sync()).rejects.toThrow()
    await expect(client.sync()).rejects.toThrow()
    expect(fetch).toHaveBeenCalledTimes(2)
    for (const [url, options] of fetch.mock.calls as unknown as [string, RequestInit][]) {
      expect(url).toBe('/v1/setup/bootstrap')
      expect(options.headers).not.toHaveProperty('Authorization')
      expect(options.method).toBeUndefined()
    }
  })

  it('shares in-flight discovery without letting one cancelled consumer cancel another', async () => {
    let resolve!: (response: Response) => void
    const fetch = vi.fn(() => new Promise<Response>(done => { resolve = done }))
    vi.stubGlobal('fetch', fetch)
    const { workflowRoot } = await import('./workflow')
    const controller = new AbortController()
    const abandoned = workflowRoot('https://engine.example', controller.signal)
    const remaining = workflowRoot('https://engine.example/')
    controller.abort()
    await expect(abandoned).rejects.toMatchObject({ name: 'AbortError' })
    resolve(new Response('{"review_enabled":true,"workflow_api_prefix":"/v1/workflow"}'))
    await expect(remaining).resolves.toBe('https://engine.example/v1/workflow')
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('uses the canonical session route for launch, restoration, and lock without exposing the launch secret in discovery', async () => {
    const fetch = backend()
    const replaceState = vi.fn()
    vi.stubGlobal('window', { location: { hash: '#login=one-use-secret', pathname: '/', search: '' }, history: { replaceState } })
    const { restoreLocalSession, endLocalSession } = await import('./auth')
    const opening = restoreLocalSession()
    expect(replaceState).toHaveBeenCalledWith(null, '', '/')
    await expect(opening).resolves.toBe(true)
    await endLocalSession()
    const requests = fetch.mock.calls as unknown as [string, RequestInit][]
    expect(requests.map(([url]) => url)).toEqual(['/v1/setup/bootstrap', '/v1/workflow/session', '/v1/workflow/session'])
    expect(JSON.stringify(requests[0])).not.toContain('one-use-secret')
    expect(requests[1][1]).toMatchObject({ method: 'POST', credentials: 'same-origin', redirect: 'error', body: '{"secret":"one-use-secret"}' })
    expect(requests[2][1]).toMatchObject({ method: 'DELETE', credentials: 'same-origin', redirect: 'error' })
  })

  it('rejects malformed discovery instead of treating an unrelated server as a legacy engine', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"workflow_api_prefix":"/v1/workflow"}')))
    const { workflowRoot } = await import('./workflow')
    await expect(workflowRoot()).rejects.toThrow('did not return a Respawned API response')
  })
})
