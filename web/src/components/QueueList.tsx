import { ChevronDown, Search, SearchX } from 'lucide-react'
import type { UIRecord } from '../data/types'
import { actionLabel, contextLine, displayName, initials } from '../presentation'

interface Props {
  records: UIRecord[]; selected: string | null; onSelect: (id: string) => void
  query: string; onQuery: (value: string) => void
  channel: string; onChannel: (value: string) => void
  view: string; onView: (value: string) => void
  sort: string; onSort: (value: string) => void
  total: number; hasMore: boolean; onMore: () => void; busy: boolean
}

export function QueueList(props: Props) {
  return <section className="queue-pane" aria-label="Records">
    <div className="queue-controls">
      <div className="search-control"><Search size={20} aria-hidden="true" />
        <input aria-label="Find a record" placeholder="Find a record" value={props.query} onChange={event => props.onQuery(event.target.value)} />
      </div>
      <div className="queue-filter-row">
        <div className="select-wrap borderless"><select aria-label="Channel" value={props.channel} onChange={event => props.onChannel(event.target.value)}>
          <option value="all">All channels</option><option value="email">Email</option><option value="sms">SMS</option>
        </select><ChevronDown size={14} /></div>
        <div className="select-wrap borderless"><select aria-label="Sort records" value={props.sort} onChange={event => props.onSort(event.target.value)}>
          <option value="priority">Highest priority</option><option value="recent">Most recent</option>
        </select><ChevronDown size={14} /></div>
      </div>
      <div className="queue-views" aria-label="Queue view">
        <button aria-pressed={props.view === 'ready'} onClick={() => props.onView('ready')}>Ready for review</button>
        <button aria-pressed={props.view === 'all'} onClick={() => props.onView('all')}>All tracked</button>
      </div>
    </div>
    <div className="queue-scroll">
      {props.records.map(record => {
        const name = displayName(record)
        const value = record.fields.find(field => field.label.toLowerCase() === 'value')?.value
        return <button className={`record-row ${props.selected === record.id ? 'is-selected' : ''}`} key={record.id}
          aria-pressed={props.selected === record.id} disabled={props.busy} onClick={() => props.onSelect(record.id)}>
          <span className="avatar">{initials(name)}</span>
          <span className="record-copy">
            <span className="record-top"><strong>{name}</strong><span className={value ? 'record-value' : 'record-action'}>{value || actionLabel(record)}</span></span>
            <span className="record-context">{contextLine(record)}</span>
            <span className="record-reason">{record.contact ? `${record.contact.channel === 'email' ? 'Email' : 'SMS'} · ` : ''}{record.reason.label}</span>
          </span>
        </button>
      })}
      {!props.records.length && <div className="empty-state queue-empty"><SearchX size={28} /><h2>No records here</h2>
        <p>{props.query || props.channel !== 'all' ? 'Try another search or channel.' : props.view === 'ready' ? 'No records currently need review. See All tracked for waiting records.' : 'Imported records will appear here.'}</p>
      </div>}
      <div className="queue-footer">{props.records.length} {props.hasMore ? 'loaded records' : `record${props.records.length === 1 ? '' : 's'}`}
        {props.hasMore && <button className="text-button" onClick={props.onMore} disabled={props.busy}>Load more ({props.total} total)</button>}
      </div>
    </div>
  </section>
}
