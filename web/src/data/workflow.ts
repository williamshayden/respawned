import { engineNetworkError, normalizeEngineUrl } from './transport'

export interface EngineBootstrap {
  review_enabled: boolean
  workflow_api_prefix?: '/v1/workflow'
}

const discoveries = new Map<string, Promise<EngineBootstrap>>()

/** Negotiate once per engine. Discovery never carries operator credentials. */
export function readEngineBootstrap(baseUrl = '', signal?: AbortSignal): Promise<EngineBootstrap> {
  signal?.throwIfAborted()
  const engine = baseUrl ? normalizeEngineUrl(baseUrl) : ''
  let pending = discoveries.get(engine)
  if (!pending) {
    pending = (async () => {
      let response: Response
      try {
        response = await fetch(`${engine}/v1/setup/bootstrap`, {
          credentials: 'omit', redirect: 'error', headers: { Accept: 'application/json' },
          signal: AbortSignal.timeout(20_000),
        })
      } catch { throw new Error(engineNetworkError(engine)) }
      if (!response.ok) throw new Error(`Could not identify this engine (HTTP ${response.status}). Check its address and server status.`)
      let payload: unknown
      try { payload = await response.json() }
      catch { throw new Error('This address did not return a Respawned API response.') }
      if (!payload || typeof payload !== 'object' || !('review_enabled' in payload) || typeof payload.review_enabled !== 'boolean') {
        throw new Error('This address did not return a Respawned API response.')
      }
      // Only this literal path opts in. Never follow an engine-supplied URL.
      return { review_enabled: payload.review_enabled,
        ...('workflow_api_prefix' in payload && payload.workflow_api_prefix === '/v1/workflow' ? { workflow_api_prefix: '/v1/workflow' as const } : {}) }
    })()
    discoveries.set(engine, pending)
    void pending.catch(() => { if (discoveries.get(engine) === pending) discoveries.delete(engine) })
  }
  if (!signal) return pending
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return }
    const cancelled = () => reject(signal.reason)
    signal.addEventListener('abort', cancelled, { once: true })
    pending.then(value => { signal.removeEventListener('abort', cancelled); if (!signal.aborted) resolve(value) },
      error => { signal.removeEventListener('abort', cancelled); if (!signal.aborted) reject(error) })
  })
}

export async function workflowRoot(baseUrl = '', signal?: AbortSignal): Promise<string> {
  const engine = baseUrl ? normalizeEngineUrl(baseUrl) : ''
  const bootstrap = await readEngineBootstrap(engine, signal)
  return `${engine}${bootstrap.workflow_api_prefix ?? '/v1/ui'}`
}
