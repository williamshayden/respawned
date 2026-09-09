import { afterEach, describe, expect, it, vi } from 'vitest'
import { importRecords, parseImport, readBootstrap, saveModel } from './setup'

afterEach(() => vi.unstubAllGlobals())

describe('environment setup', () => {
  it('previews activity-only imports without dropping their source fields', () => {
    const event = { id: 'source:event:1', opportunity_id: 'source:record:1', summary: 'Actual event', direction: 'inbound' }
    expect(parseImport(JSON.stringify({ activities: [event] }))).toEqual({ opportunities: [], activities: [event] })
    expect(() => parseImport('{"activities":null}')).toThrow('arrays')
    expect(() => parseImport('{"opportunities":[]}')).toThrow('at least one')
    expect(() => parseImport('{"records":[{}]}')).toThrow('top-level fields')
    expect(() => parseImport('{"opportunities":[null]}')).toThrow('JSON object')
  })

  it('bounds imported batches by count and encoded size', () => {
    expect(() => parseImport(JSON.stringify({ activities: Array(1001).fill({ id: 'event' }) }))).toThrow('1,000')
    expect(() => parseImport(JSON.stringify({ opportunities: [{ summary: '🌱'.repeat(500_000) }] }))).toThrow('2 MB')
  })

  it('saves only non-secret model fields and sends review authority in the header', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ready: true })))
    vi.stubGlobal('fetch', fetch)
    const settings = { base_url: 'http://localhost:11434/v1', model_alias: 'local-model', timeout_seconds: 30, api_key_env: 'RESPAWNED_MODEL_API_KEY' as const }
    await saveModel('session-review-access', settings)
    expect(fetch).toHaveBeenCalledOnce()
    expect(fetch.mock.calls[0][0]).toBe('/v1/ui/setup/model')
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'PUT', headers: { Authorization: 'Bearer session-review-access' }, body: JSON.stringify(settings) })
    expect(fetch.mock.calls[0][1].body).not.toContain('session-review-access')
  })

  it('reads bootstrap without authority and preserves server validation field paths', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response('{"review_enabled":false}'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: [{ loc: ['body', 'opportunities', 0, 'created_at'], msg: 'Timestamp needs a timezone' }] }), { status: 422 }))
    vi.stubGlobal('fetch', fetch)
    expect(await readBootstrap()).toEqual({ review_enabled: false })
    expect(fetch.mock.calls[0][1].headers).not.toHaveProperty('Authorization')
    await expect(importRecords('session-access', { opportunities: [{}], activities: [] })).rejects.toThrow('opportunities.0.created_at: Timestamp needs a timezone')
  })
})
