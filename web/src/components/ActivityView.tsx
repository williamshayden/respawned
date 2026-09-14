import { useEffect, useRef, useState } from 'react'
import { ExternalLink } from 'lucide-react'
import type { ReviewClient, UIRecord } from '../data/types'
import { safeSource, shortDate } from '../presentation'

export function ActivityView({ client, onSelect }: { client: ReviewClient; onSelect: (id: string) => void }) {
  const [records, setRecords] = useState<UIRecord[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [owner, setOwner] = useState<ReviewClient | null>(null)
  const generation = useRef(0)
  const offset = useRef(0)
  const load = async (reset = false) => {
    const request = ++generation.current
    setLoading(true); setError(null)
    try {
      const page = await client.listRecords(reset ? 0 : offset.current, { view: 'all' })
      if (request !== generation.current) return
      setRecords(previous => reset ? page.items : [...new Map([...previous, ...page.items].map(record => [record.id, record])).values()])
      offset.current = (reset ? 0 : offset.current) + page.items.length
      setTotal(page.total); setHasMore(page.has_more); setOwner(client)
    } catch (reason) {
      if (request === generation.current) setError(reason instanceof Error ? reason.message : 'Could not load activity.')
    } finally { if (request === generation.current) setLoading(false) }
  }
  useEffect(() => {
    setRecords([]); setTotal(0); setHasMore(false); offset.current = 0
    void load(true)
    return () => { generation.current++ }
  }, [client])
  const visible = owner === client ? records : []
  const activities = visible.flatMap(record => record.activities.map(activity => ({ record, activity })))
    .sort((a, b) => b.activity.occurred_at.localeCompare(a.activity.occurred_at))
  return <div className="auxiliary-view"><div className="aux-heading"><p>{hasMore
    ? `Imported events from ${visible.length} of ${total} records, ordered by date within this loaded subset.`
    : 'Imported events, ordered by date.'}</p></div>
    {error && <p className="feedback is-error" role="alert">{error} <button className="text-button" onClick={() => void load(!visible.length)}>Retry</button></p>}
    <div className="activity-feed">{activities.map(({ record, activity }) => <article key={`${record.id}:${activity.id}`}>
      <time dateTime={activity.occurred_at}>{shortDate(activity.occurred_at)}</time><div><h3>{activity.label}</h3>
        <button className="text-button" onClick={() => onSelect(record.id)}>{record.title}</button>{activity.summary && <p>{activity.summary}</p>}
        <span className="activity-classification">{activity.classification === 'unknown' ? 'Classification unknown' : activity.classification === 'human' ? 'Human' : 'Automated'}</span>
      </div>{safeSource(activity.source_url) && <a href={safeSource(activity.source_url)} target="_blank" rel="noreferrer" aria-label={`Source for ${activity.label}`}><ExternalLink size={17} /></a>}
    </article>)}</div>
    {loading && <p role="status">Loading activity…</p>}
    {!loading && !error && !activities.length && <div className="empty-state"><h2>{hasMore ? 'No activity in the loaded records' : 'No activity yet'}</h2></div>}
    {hasMore && <div className="queue-footer"><button className="button" disabled={loading} onClick={() => void load()}>Load more activity ({visible.length} of {total} records)</button></div>}
  </div>
}
