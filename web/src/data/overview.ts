import { workflowRoot } from './workflow'
import { useCallback, useEffect, useRef, useState } from 'react'
import { accessHeaders, type ReviewAccess } from './auth'
import { engineNetworkError, engineRequestOptions, type EngineConnection } from './connections'

export interface WorkspaceOverview {
  id: string
  name: string
  description: string
  kinds: string[]
  counts: { records: number; ready: number; pending_drafts: number; reply_contacts: number; pending_outbox: number }
}
export interface Overview { generated_at: string; source_freshness: 'unknown'; workspaces: WorkspaceOverview[] }
export type OverviewFailure = 'access' | 'offline' | 'unsupported' | 'invalid'
export class OverviewError extends Error {
  constructor(public readonly kind: OverviewFailure, message: string) { super(message) }
}
export interface EngineOverviewState {
  baseUrl: string
  access: ReviewAccess
  loading: boolean
  data?: Overview
  checkedAt?: string
  failure?: OverviewFailure
  error?: string
}

function validOverview(value: unknown): value is Overview {
  if (!value || typeof value !== 'object') return false
  const overview = value as Overview
  if (typeof overview.generated_at !== 'string' || !Number.isFinite(Date.parse(overview.generated_at)) || overview.source_freshness !== 'unknown' || !Array.isArray(overview.workspaces)) return false
  const ids = new Set<string>()
  return overview.workspaces.every(workspace => {
    if (!workspace || typeof workspace !== 'object' || typeof workspace.id !== 'string' || !workspace.id || ids.has(workspace.id)) return false
    ids.add(workspace.id)
    return typeof workspace.name === 'string' && typeof workspace.description === 'string' &&
      Array.isArray(workspace.kinds) && workspace.kinds.every(kind => typeof kind === 'string') &&
      !!workspace.counts && ['records', 'ready', 'pending_drafts', 'reply_contacts', 'pending_outbox'].every(key => {
        const count = workspace.counts[key as keyof WorkspaceOverview['counts']]
        return Number.isSafeInteger(count) && count >= 0
      })
  })
}

/** Monitoring only reads the engine's canonical summary. It never synchronizes or drafts. */
export async function readOverview(baseUrl: string, access: ReviewAccess, signal?: AbortSignal): Promise<Overview> {
  if (!access) throw new OverviewError('access', 'Unlock this engine to monitor its workspaces.')
  let response: Response
  try {
    const transport = engineRequestOptions(access, baseUrl)
    const root = await workflowRoot(baseUrl, signal)
    response = await fetch(`${root}/overview`, {
      ...transport, headers: { Accept: 'application/json', ...accessHeaders(access) }, signal,
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new OverviewError('offline', engineNetworkError(baseUrl))
  }
  if (response.status === 401 || response.status === 403) throw new OverviewError('access', 'Access expired or was not accepted. Unlock this engine in Connections.')
  if (response.status === 404) throw new OverviewError('unsupported', 'This engine does not provide workspace monitoring. Update its Respawned app.')
  if (!response.ok) throw new OverviewError('offline', 'The engine could not refresh its overview. Check its server and database status.')
  let payload: unknown
  try { payload = await response.json() }
  catch { throw new OverviewError('invalid', 'The engine returned an unreadable overview. Check its URL and app version.') }
  if (!validOverview(payload)) throw new OverviewError('invalid', 'The engine returned an incompatible overview. Check its app version.')
  return payload
}

export function useOverview(connections: EngineConnection[], accessById: Record<string, ReviewAccess>, automatic: boolean) {
  const [states, setStates] = useState<Record<string, EngineOverviewState>>({})
  const cycle = useRef(0)
  const requests = useRef<AbortController[]>([])
  const refresh = useCallback(() => {
    const generation = ++cycle.current
    requests.current.forEach(controller => controller.abort())
    requests.current = []
    const connectedIds = new Set(connections.map(connection => connection.id))
    setStates(previous => Object.fromEntries(Object.entries(previous).filter(([id]) => connectedIds.has(id))))
    for (const connection of connections) {
      const access = accessById[connection.id] ?? null
      const identity = { baseUrl: connection.baseUrl, access }
      setStates(previous => {
        const last = previous[connection.id]
        const retained = last?.baseUrl === connection.baseUrl && last.access === access ? last : identity
        return { ...previous, [connection.id]: { ...retained, loading: !!access, failure: access ? undefined : 'access', error: access ? undefined : 'Unlock this engine to monitor its workspaces.' } }
      })
      if (!access) continue
      const controller = new AbortController()
      requests.current.push(controller)
      let timedOut = false
      const timeout = window.setTimeout(() => { timedOut = true; controller.abort() }, 20_000)
      void readOverview(connection.baseUrl, access, controller.signal).then(data => {
        if (cycle.current !== generation || controller.signal.aborted) return
        setStates(previous => ({ ...previous, [connection.id]: { ...identity, loading: false, data, checkedAt: new Date().toISOString() } }))
      }).catch(error => {
        if (cycle.current !== generation || (controller.signal.aborted && !timedOut)) return
        setStates(previous => ({ ...previous, [connection.id]: {
          ...previous[connection.id], ...identity, loading: false,
          failure: error instanceof OverviewError ? error.kind : 'offline',
          error: timedOut ? 'This engine took too long to respond. Check its connection and try again.' : error instanceof Error ? error.message : 'Could not refresh this engine.',
        } }))
      }).finally(() => window.clearTimeout(timeout))
    }
  }, [connections, accessById])

  useEffect(() => {
    refresh()
    return () => { cycle.current++; requests.current.forEach(controller => controller.abort()) }
  }, [refresh])
  useEffect(() => {
    if (!automatic) return
    const interval = window.setInterval(() => { if (document.visibilityState === 'visible') refresh() }, 30_000)
    const resumed = () => { if (document.visibilityState === 'visible') refresh() }
    document.addEventListener('visibilitychange', resumed)
    return () => { window.clearInterval(interval); document.removeEventListener('visibilitychange', resumed) }
  }, [automatic, refresh])

  // Never paint another destination's data while effects clean up an old request.
  const current = Object.fromEntries(connections.map(connection => {
    const access = accessById[connection.id] ?? null
    const state = states[connection.id]
    return [connection.id, state?.baseUrl === connection.baseUrl && state.access === access ? state : undefined]
  })) as Record<string, EngineOverviewState | undefined>
  return { states: current, refresh }
}

export const WATCH_STORAGE_KEY = 'respawned.overview.watches.v1'
export type WatchPreferences = Record<string, boolean>
export const watchKey = (connectionId: string, workspaceId: string) => JSON.stringify([connectionId, workspaceId])
export const isWatched = (preferences: WatchPreferences, connectionId: string, workspaceId: string) => preferences[watchKey(connectionId, workspaceId)] ?? workspaceId === 'all'
export function readWatchPreferences(raw: string | null): WatchPreferences {
  try {
    const value: unknown = JSON.parse(raw ?? '{}')
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
    return Object.fromEntries(Object.entries(value).filter(([key, watched]) => {
      try { const tuple: unknown = JSON.parse(key); return typeof watched === 'boolean' && Array.isArray(tuple) && tuple.length === 2 && tuple.every(part => typeof part === 'string') }
      catch { return false }
    }))
  } catch { return {} }
}
