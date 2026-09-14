import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { InboxResult, OutboxItem, RecordQuery, ReviewClient, UIConfig, UIRecord } from './data/types'
import { actionable } from './presentation'
import { matchesRecordQuery } from './data/recordQuery'

export function useReview(client: ReviewClient | null, selectedRecordId: string | null = null, filters: RecordQuery = {}) {
  const queryKey = JSON.stringify(filters)
  const query = useMemo<RecordQuery>(() => JSON.parse(queryKey), [queryKey])
  const [records, setRecords] = useState<UIRecord[]>([])
  const [outbox, setOutbox] = useState<OutboxItem[]>([])
  const [inbox, setInbox] = useState<InboxResult | null>(null)
  const [config, setConfig] = useState<UIConfig | null>(null)
  const [manualDraftSupported, setManualDraftSupported] = useState(false)
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [counts, setCounts] = useState<{ tracked: number; ready: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [snapshotClient, setSnapshotClient] = useState<ReviewClient | null>(null)
  const [snapshotQuery, setSnapshotQuery] = useState<string | null>(null)
  const generation = useRef(0)
  const currentClient = useRef(client)
  const inFlight = useRef<object | null>(null)
  const detailId = useRef<string | null>(null)
  const selection = useRef(selectedRecordId)
  selection.current = selectedRecordId
  const nextOffset = useRef(0)
  const nextInboxOffset = useRef(0)
  const loadedClient = useRef<ReviewClient | null>(null)
  const relatedClient = useRef<ReviewClient | null>(null)
  const currentQuery = useRef(queryKey)
  currentClient.current = client
  currentQuery.current = queryKey

  const load = useCallback(async (offset = 0, refreshRelated = true) => {
    // Old workspace requests must not invalidate the active workspace load.
    if (!client || currentClient.current !== client) return
    const request = ++generation.current
    const [page, related] = await Promise.all([
      client.listRecords(offset, query),
      refreshRelated ? Promise.all([client.config(), client.listOutbox(), client.listInbox(), client.supportsManualDraft()]) : null,
    ])
    if (request !== generation.current || currentClient.current !== client || currentQuery.current !== queryKey) return
    const selected = !offset && detailId.current && !page.items.some(record => record.id === detailId.current)
      ? await client.getRecord(detailId.current) : null
    if (request !== generation.current || currentClient.current !== client || currentQuery.current !== queryKey) return
    const items = selected && matchesRecordQuery(selected, query) ? [...page.items, selected] : page.items
    setRecords(previous => offset ? [...new Map([...previous, ...items].map(record => [record.id, record])).values()] : items)
    // A directly opened detail is not part of the page cursor.
    nextOffset.current = offset + page.items.length
    setTotal(page.total); setHasMore(page.has_more)
    setCounts(page.counts ?? { tracked: page.total, ready: page.items.filter(actionable).length })
    if (related) {
      const [nextConfig, nextOutbox, nextInbox, nextManualDraftSupported] = related
      setConfig(nextConfig); setOutbox(nextOutbox); setInbox(nextInbox)
      nextInboxOffset.current = nextInbox.items.length
      setManualDraftSupported(nextManualDraftSupported)
      relatedClient.current = client
    }
    setSnapshotClient(client)
    setSnapshotQuery(queryKey)
  }, [client, query, queryKey])

  useEffect(() => {
    let active = true
    const changedClient = loadedClient.current !== client
    loadedClient.current = client
    if (changedClient) {
      detailId.current = selection.current
      setOutbox([]); setInbox(null); setConfig(null); setCounts(null); setManualDraftSupported(false)
      nextInboxOffset.current = 0
    }
    nextOffset.current = 0; inFlight.current = null
    setLoading(true); setBusy(null); setError(null); setMessage(null); setRecords([])
    setTotal(0); setHasMore(false)
    if (!client) { setLoading(false); return }
    load(0, relatedClient.current !== client).catch(reason => { if (active && currentClient.current === client) setError(reason instanceof Error ? reason.message : 'Could not load this workspace.') })
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
  const ownsRecords = ownsSnapshot && snapshotQuery === queryKey
  return { records: ownsRecords ? records : [], outbox: ownsSnapshot ? outbox : [],
    inbox: ownsSnapshot ? inbox : null, config: ownsSnapshot ? config : null,
    total: ownsRecords ? total : 0, hasMore: ownsRecords && hasMore,
    trackedCount: ownsSnapshot ? counts?.tracked ?? 0 : 0, readyCount: ownsSnapshot ? counts?.ready ?? 0 : 0,
    loading, busy, error, recordsError: !ownsRecords ? error : null, message, setMessage, setError, canWriteDraft: ownsSnapshot && manualDraftSupported,
    rememberSelection: (id: string | null) => { detailId.current = id },
    openRecord: (id: string) => operate('Opening record', async () => {
      if (!client) return
      const record = await client.getRecord(id)
      if (currentClient.current !== client) return
      detailId.current = id
      setRecords(previous => [...new Map([...previous, record].map(item => [item.id, item])).values()])
    }, undefined, false),
    retry: () => operate('Reloading', async () => undefined),
    more: () => operate('Loading', () => load(nextOffset.current, false), undefined, false),
    moreInbox: () => operate('Loading replies', async () => {
      if (!client || !inbox?.has_more) return
      const next = await client.listInbox(nextInboxOffset.current)
      if (currentClient.current !== client) return
      const items = [...new Map([...inbox.items, ...next.items].map(item => [`${item.contact_key}:${item.channel}`, item])).values()]
      if (next.has_more && items.length === inbox.items.length) throw new Error('This engine does not support reply pagination. Update Respawned on that engine to view the remaining replies.')
      nextInboxOffset.current += next.items.length
      setInbox({ ...next, items })
    }, undefined, false),
    operate,
  }
}
