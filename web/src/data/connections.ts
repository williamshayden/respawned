import { useCallback, useMemo, useRef, useState } from 'react'
import { accessHeaders, isLocalSession, type ReviewAccess } from './auth'
import { engineNetworkError, engineRequestOptions, normalizeEngineUrl } from './transport'
import { workflowRoot } from './workflow'
export { engineNetworkError, engineRequestOptions, normalizeEngineUrl } from './transport'

export interface EngineConnection { id: string; name: string; baseUrl: string }
export const LOCAL_ENGINE: EngineConnection = { id: 'local', name: 'This engine', baseUrl: '' }
export const CONNECTIONS_STORAGE_KEY = 'respawned.engine-connections.v1'
type ConnectionStorage = Pick<Storage, 'getItem' | 'setItem'>

function browserStorage(): ConnectionStorage | undefined {
  try { return typeof window === 'undefined' ? undefined : window.localStorage }
  catch { return undefined }
}

export function readSavedConnections(storage: ConnectionStorage | undefined = browserStorage()): EngineConnection[] {
  try {
    const parsed: unknown = JSON.parse(storage?.getItem(CONNECTIONS_STORAGE_KEY) ?? '[]')
    if (!Array.isArray(parsed)) return [LOCAL_ENGINE]
    const ids = new Set(['local'])
    const urls = new Set<string>()
    const remote: EngineConnection[] = []
    for (const item of parsed) {
      if (!item || typeof item !== 'object' || typeof item.id !== 'string' || !item.id || ids.has(item.id) || typeof item.name !== 'string' || !item.name.trim() || typeof item.baseUrl !== 'string') continue
      try {
        const baseUrl = normalizeEngineUrl(item.baseUrl)
        if (urls.has(baseUrl)) continue
        ids.add(item.id); urls.add(baseUrl)
        remote.push({ id: item.id, name: item.name.trim().slice(0, 120), baseUrl })
      } catch { /* Ignore invalid or obsolete saved entries. */ }
    }
    return [LOCAL_ENGINE, ...remote]
  } catch { return [LOCAL_ENGINE] }
}

export function saveConnections(connections: EngineConnection[], storage: ConnectionStorage | undefined = browserStorage()): void {
  // Explicit fields keep credentials and server data out of persistent storage.
  try { storage?.setItem(CONNECTIONS_STORAGE_KEY, JSON.stringify(connections.filter(item => item.id !== 'local').map(({ id, name, baseUrl }) => ({ id, name, baseUrl })))) }
  catch { /* Connections remain usable in memory if storage is unavailable. */ }
}

export async function verifyEngineAccess(baseUrl: string, token: string): Promise<void> {
  if (!token.trim()) throw new Error('Enter the access token configured on this engine.')
  let response: Response
  try {
    const root = await workflowRoot(baseUrl)
    response = await fetch(`${root}/setup`, {
      ...engineRequestOptions(token.trim(), baseUrl),
      headers: { Accept: 'application/json', ...accessHeaders(token.trim()) },
      signal: AbortSignal.timeout(20_000),
    })
  } catch { throw new Error(engineNetworkError(baseUrl)) }
  if (response.status === 401 || response.status === 403) throw new Error('This engine did not accept the token. Check RESPAWNED_REVIEW_TOKEN on that server and its allowed browser origins.')
  if (!response.ok) throw new Error(`The engine returned HTTP ${response.status}. Check the API root and that this server supports the Respawned UI.`)
  let payload: unknown
  try { payload = await response.json() }
  catch { throw new Error('This address did not return a Respawned API response. Enter the engine root URL.') }
  if (!payload || typeof payload !== 'object' || !('review' in payload) || !('database' in payload)) throw new Error('This address did not return a Respawned API response. Enter the engine root URL.')
}

export function useConnections() {
  const [connections, updateConnections] = useState<EngineConnection[]>(readSavedConnections)
  const [accessById, updateAccess] = useState<Record<string, ReviewAccess>>({})
  const [activeId, updateActiveId] = useState('local')
  const current = useRef(connections)
  const accessGeneration = useRef<Record<string, number>>({})
  current.current = connections

  const replaceConnections = useCallback((next: EngineConnection[]) => {
    current.current = next
    saveConnections(next)
    updateConnections(next)
  }, [])
  const setActiveId = useCallback((id: string) => {
    if (current.current.some(item => item.id === id)) updateActiveId(id)
  }, [])
  const setAccess = useCallback((id: string, access: ReviewAccess) => {
    const connection = current.current.find(item => item.id === id)
    if (!connection) return
    if (connection.baseUrl && isLocalSession(access)) throw new Error('A local session can only unlock this engine.')
    accessGeneration.current[id] = (accessGeneration.current[id] ?? 0) + 1
    updateAccess(previous => ({ ...previous, [id]: access }))
  }, [])
  const add = useCallback(async (name: string, url: string, token: string) => {
    const trimmedName = name.trim()
    if (!trimmedName || trimmedName.length > 120) throw new Error('Choose an engine name between 1 and 120 characters.')
    const baseUrl = normalizeEngineUrl(url)
    const duplicate = () => current.current.some(item => item.baseUrl === baseUrl) || (typeof window !== 'undefined' && baseUrl === window.location.origin)
    if (duplicate()) throw new Error('This engine is already in your connections. Open or unlock its existing connection.')
    await verifyEngineAccess(baseUrl, token)
    if (duplicate()) throw new Error('This engine was already connected. Open its existing connection.')
    const id = crypto.randomUUID()
    replaceConnections([...current.current, { id, name: trimmedName, baseUrl }])
    setAccess(id, token.trim())
    return id
  }, [replaceConnections, setAccess])
  const unlock = useCallback(async (id: string, token: string) => {
    const connection = current.current.find(item => item.id === id)
    if (!connection) throw new Error('This engine connection was removed.')
    const generation = (accessGeneration.current[id] ?? 0) + 1
    accessGeneration.current[id] = generation
    await verifyEngineAccess(connection.baseUrl, token)
    if (accessGeneration.current[id] !== generation || !current.current.some(item => item.id === id)) return
    setAccess(id, token.trim())
  }, [setAccess])
  const lock = useCallback((id: string) => setAccess(id, null), [setAccess])
  const remove = useCallback((id: string) => {
    if (id === 'local') return
    accessGeneration.current[id] = (accessGeneration.current[id] ?? 0) + 1
    replaceConnections(current.current.filter(item => item.id !== id))
    updateAccess(previous => { const next = { ...previous }; delete next[id]; return next })
    updateActiveId(previous => previous === id ? 'local' : previous)
  }, [replaceConnections])
  const active = useMemo(() => connections.find(item => item.id === activeId) ?? LOCAL_ENGINE, [connections, activeId])
  return { connections, accessById, activeId: active.id, active, setActiveId, add, unlock, lock, remove, setAccess }
}
