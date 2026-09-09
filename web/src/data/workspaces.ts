import { useCallback, useEffect, useRef, useState } from 'react'

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

export async function workspaceRequest<T>(token: string, path = '', method = 'GET', body?: WorkspaceInput): Promise<T> {
  const response = await fetch(`/v1/ui/workspaces${path}`, {
    method, headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  })
  if (response.status === 204) return undefined as T
  const payload = await response.json()
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Could not save workspace. Check the fields and your connection.')
  return payload as T
}

export function useWorkspaces(token: string) {
  const [data, setData] = useState<WorkspaceList>({ items: [], available_kinds: [], scope: 'shared_engine' })
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const activeToken = useRef(token)
  const generation = useRef(0)
  activeToken.current = token
  const reload = useCallback(async () => {
    if (!token || activeToken.current !== token) return
    const request = ++generation.current
    setLoading(true); setError(null)
    try {
      const next = await workspaceRequest<WorkspaceList>(token)
      if (activeToken.current === token && request === generation.current) setData(next)
    } catch (reason) {
      if (activeToken.current === token && request === generation.current) setError(reason instanceof Error ? reason.message : 'Could not load workspaces.')
    } finally {
      if (activeToken.current === token && request === generation.current) setLoading(false)
    }
  }, [token])
  useEffect(() => {
    setData({ items: [], available_kinds: [], scope: 'shared_engine' }); setError(null); setLoading(false)
    void reload()
    return () => { generation.current++ }
  }, [reload])
  return { ...data, error, loading, reload }
}
