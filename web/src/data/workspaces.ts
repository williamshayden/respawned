import { useCallback, useEffect, useRef, useState } from 'react'
import { accessHeaders, type ReviewAccess } from './auth'
import { engineNetworkError, engineRequestOptions } from './connections'

export interface Workspace {
  id: string
  name: string
  description: string
  kinds: string[]
  created_at: string
  updated_at: string
}
export type WorkspaceInput = Pick<Workspace, 'name' | 'description' | 'kinds'>
export interface WorkspaceList { items: Workspace[]; available_kinds: string[]; scope: 'shared_engine' }

export async function workspaceRequest<T>(token: ReviewAccess, path = '', method = 'GET', body?: WorkspaceInput, baseUrl = ''): Promise<T> {
  let response: Response
  const deadline = AbortSignal.timeout(20_000)
  try { response = await fetch(`${baseUrl}/v1/ui/workspaces${path}`, {
    ...engineRequestOptions(token, baseUrl),
    signal: deadline,
    method, headers: { ...accessHeaders(token), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  }) } catch {
    if (deadline.aborted) throw new Error(method === 'GET'
      ? 'The engine took too long to respond. Check its connection and refresh.'
      : 'The request timed out. Reload to check whether it completed before retrying.')
    throw new Error(engineNetworkError(baseUrl))
  }
  if (response.status === 204) return undefined as T
  const payload = await response.json()
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Could not save workspace. Check the fields and your connection.')
  return payload as T
}

export function useWorkspaces(token: ReviewAccess, baseUrl = '') {
  const [data, setData] = useState<WorkspaceList>({ items: [], available_kinds: [], scope: 'shared_engine' })
  const [dataOwner, setDataOwner] = useState<{ token: ReviewAccess; baseUrl: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const activeToken = useRef({ token, baseUrl })
  const generation = useRef(0)
  activeToken.current = { token, baseUrl }
  const reload = useCallback(async () => {
    const isCurrent = () => activeToken.current.token === token && activeToken.current.baseUrl === baseUrl
    if (!token || !isCurrent()) return
    const request = ++generation.current
    setLoading(true); setError(null)
    try {
      const next = await workspaceRequest<WorkspaceList>(token, '', 'GET', undefined, baseUrl)
      if (isCurrent() && request === generation.current) { setData(next); setDataOwner({ token, baseUrl }) }
    } catch (reason) {
      if (isCurrent() && request === generation.current) setError(reason instanceof Error ? reason.message : 'Could not load workspaces.')
    } finally {
      if (isCurrent() && request === generation.current) setLoading(false)
    }
  }, [token, baseUrl])
  useEffect(() => {
    setData({ items: [], available_kinds: [], scope: 'shared_engine' }); setError(null); setLoading(false)
    void reload()
    return () => { generation.current++ }
  }, [reload])
  const ownsData = dataOwner?.token === token && dataOwner?.baseUrl === baseUrl
  return { ...data, items: ownsData ? data.items : [], available_kinds: ownsData ? data.available_kinds : [], error, loading, reload }
}
