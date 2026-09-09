import { useCallback, useEffect, useRef, useState } from 'react'
import type { InboxResult, OutboxItem, ReviewClient, UIConfig, UIRecord } from './data/types'

export function useReview(client: ReviewClient | null, selectedRecordId: string | null = null) {
  const [records, setRecords] = useState<UIRecord[]>([])
  const [outbox, setOutbox] = useState<OutboxItem[]>([])
  const [inbox, setInbox] = useState<InboxResult | null>(null)
  const [config, setConfig] = useState<UIConfig | null>(null)
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [snapshotClient, setSnapshotClient] = useState<ReviewClient | null>(null)
  const generation = useRef(0)
  const currentClient = useRef(client)
  const inFlight = useRef<object | null>(null)
  const detailId = useRef<string | null>(null)
  const selection = useRef(selectedRecordId)
  selection.current = selectedRecordId
  const nextOffset = useRef(0)
  currentClient.current = client

  const load = useCallback(async (offset = 0) => {
    // Old workspace requests must not invalidate the active workspace load.
    if (!client || currentClient.current !== client) return
    const request = ++generation.current
    const [page, nextConfig, nextOutbox, nextInbox] = await Promise.all([
      client.listRecords(offset), client.config(), client.listOutbox(), client.listInbox(),
    ])
    if (request !== generation.current || currentClient.current !== client) return
    const selected = !offset && detailId.current && !page.items.some(record => record.id === detailId.current)
      ? await client.getRecord(detailId.current) : null
    if (request !== generation.current || currentClient.current !== client) return
    const items = selected ? [...page.items, selected] : page.items
    setRecords(previous => offset ? [...new Map([...previous, ...items].map(record => [record.id, record])).values()] : items)
    // A directly opened detail is not part of the page cursor.
    nextOffset.current = offset + page.items.length
    setConfig(nextConfig); setOutbox(nextOutbox); setInbox(nextInbox); setTotal(page.total); setHasMore(page.has_more)
    setSnapshotClient(client)
  }, [client])

  useEffect(() => {
    let active = true
    detailId.current = selection.current; nextOffset.current = 0; inFlight.current = null
    setLoading(true); setBusy(null); setError(null); setMessage(null); setRecords([]); setOutbox([]); setInbox(null); setConfig(null)
    setTotal(0); setHasMore(false)
    if (!client) { setLoading(false); return }
    load().catch(reason => { if (active && currentClient.current === client) setError(reason instanceof Error ? reason.message : 'Could not load this workspace.') })
      .finally(() => { if (active && currentClient.current === client) setLoading(false) })
    return () => { active = false; generation.current++ }
  }, [client, load])

  const operate = useCallback(async (label: string, operation: () => Promise<unknown>, success?: string, refresh = true) => {
    if (!client || inFlight.current || currentClient.current !== client) return false
    const request = {}
    inFlight.current = request
    const isCurrent = () => currentClient.current === client && inFlight.current === request
    setBusy(label); setError(null); setMessage(null)
    try {
      await operation()
      if (!isCurrent()) return false
      if (refresh) await load()
      if (!isCurrent()) return false
      if (success) setMessage(success)
      return true
    } catch (reason) {
      if (isCurrent()) setError(reason instanceof Error ? reason.message : 'That action could not be completed. Please try again.')
      return false
    } finally {
      if (isCurrent()) { inFlight.current = null; setBusy(null) }
    }
  }, [client, load])

  // React effects reset state after rendering. Never expose the previous engine's
  // records beside a new engine's mutation client during that intervening render.
  const ownsSnapshot = snapshotClient === client
  return { records: ownsSnapshot ? records : [], outbox: ownsSnapshot ? outbox : [],
    inbox: ownsSnapshot ? inbox : null, config: ownsSnapshot ? config : null,
    total: ownsSnapshot ? total : 0, hasMore: ownsSnapshot && hasMore,
    loading, busy, error, message, setMessage, setError,
    rememberSelection: (id: string | null) => { detailId.current = id },
    openRecord: (id: string) => operate('Opening record', async () => {
      if (!client) return
      const record = await client.getRecord(id)
      if (currentClient.current !== client) return
      detailId.current = id
      setRecords(previous => [...new Map([...previous, record].map(item => [item.id, item])).values()])
    }, undefined, false),
    reload: () => operate('Refreshing', async () => { await client?.sync() }, 'Queue refreshed.'),
    retry: () => operate('Reloading', async () => undefined),
    more: () => operate('Loading', () => load(nextOffset.current), undefined, false),
    operate,
  }
}
