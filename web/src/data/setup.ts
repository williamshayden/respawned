import { accessHeaders, type ReviewAccess } from './auth'
import { engineNetworkError, engineRequestOptions } from './connections'

export interface ModelSettings {
  backend?: 'openai_compatible' | 'codex_cli'
  base_url: string
  model_alias: string
  timeout_seconds: number
  api_key_env: 'LITELLM_MASTER_KEY' | 'RESPAWNED_MODEL_API_KEY'
}

export interface ModelStatus extends ModelSettings {
  source: 'saved' | 'environment'
  key_configured: boolean
  ready: boolean
  verified: false
  error: string | null
}

export interface SetupStatus {
  database: { status: 'ready' | 'unavailable'; message: string }
  review: { enabled: boolean; authentication: 'bearer' | 'local_session'; token_env: string }
  model: ModelStatus
  outbox: { mode: 'export_only'; automatic_delivery: false; export_url: string } | {
    mode: 'api_and_export'
    automatic_delivery: false
    export_url: string
    pending_url: string
    receipt_url: string
    token_env: string
    token_configured: boolean
  }
  sources: { mode: 'api_import'; import_url: string }
}

export interface ImportPayload {
  opportunities: Record<string, unknown>[]
  activities: Record<string, unknown>[]
}

export interface ImportResult {
  opportunities_upserted: number
  activities_inserted: number
}

export const IMPORT_TEMPLATE = JSON.stringify({
  opportunities: [{
    id: 'your-stable-record-id',
    kind: 'generic',
    title: 'Replace with your record title',
    status: 'open',
    created_at: '2026-09-09T09:00:00Z',
    contact_key: 'your-stable-contact-id',
    contact_name: 'Replace with the contact name',
    contact_email: 'replace-with-contact@example.com',
    preferred_channel: 'email',
    context: { summary: 'Replace with the current facts about this record.' },
  }],
  activities: [{
    id: 'your-stable-activity-id',
    opportunity_id: 'your-stable-record-id',
    type: 'message_sent',
    occurred_at: '2026-09-09T09:00:00Z',
    channel: 'email',
    direction: 'outbound',
    classification: 'human',
    summary: 'Replace with a summary of the actual message sent.',
  }],
}, null, 2)

/** Preview the batch shape; the server validates every source field before importing. */
export function parseImport(text: string): ImportPayload {
  let value: unknown
  try { value = JSON.parse(text) }
  catch { throw new Error('Enter valid JSON or choose a JSON file.') }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Use a JSON object with opportunities and activities arrays.')
  const data = value as Record<string, unknown>
  if (Object.keys(data).some(key => key !== 'opportunities' && key !== 'activities')) throw new Error('The top-level fields must be opportunities and activities.')
  const opportunities = data.opportunities === undefined ? [] : data.opportunities
  const activities = data.activities === undefined ? [] : data.activities
  if (!Array.isArray(opportunities) || !Array.isArray(activities)) throw new Error('Opportunities and activities must be arrays.')
  if (!opportunities.length && !activities.length) throw new Error('Add at least one record or activity before importing.')
  if (opportunities.length + activities.length > 1000) throw new Error('Import at most 1,000 records and activities at a time. Split this batch into smaller files.')
  if ([...opportunities, ...activities].some(item => !item || typeof item !== 'object' || Array.isArray(item))) throw new Error('Each record and activity must be a JSON object.')
  if (new TextEncoder().encode(JSON.stringify({ opportunities, activities })).length > 2_000_000) throw new Error('Import JSON must be at most 2 MB. Split this batch into smaller files.')
  return { opportunities, activities }
}

function responseError(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== 'object') return fallback
  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return fallback
  const messages = detail.flatMap(item => {
    if (!item || typeof item !== 'object' || typeof item.msg !== 'string') return []
    const path = Array.isArray(item.loc) ? item.loc.filter((part: unknown) => part !== 'body').join('.') : ''
    return [path ? `${path}: ${item.msg}` : item.msg]
  })
  return messages.length ? messages.join(' ') : fallback
}

async function request<T>(path: string, token: ReviewAccess, method = 'GET', body?: unknown, signal?: AbortSignal, baseUrl = ''): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...accessHeaders(token) }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let response: Response
  const deadline = AbortSignal.timeout(20_000)
  const requestSignal = signal ? AbortSignal.any([signal, deadline]) : deadline
  try {
    response = await fetch(`${baseUrl}${path}`, { ...engineRequestOptions(token, baseUrl), method, headers, signal: requestSignal, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  } catch (error) {
    if (signal?.aborted) throw error
    if (deadline.aborted) throw new Error(method === 'GET'
      ? 'The engine took too long to respond. Check its connection and refresh.'
      : 'The request timed out. Reload to check whether it completed before retrying.')
    if (error instanceof Error && error.name === 'AbortError') throw error
    throw new Error(engineNetworkError(baseUrl))
  }
  let payload: unknown
  try { payload = await response.json() }
  catch { throw new Error('The server returned an unreadable response. Check the server and try again.') }
  if (!response.ok) throw new Error(responseError(payload, response.status === 401 || response.status === 403
    ? 'Review access was not accepted. Reconnect with the token configured on this server.'
    : 'The request could not be completed. Please try again.'))
  return payload as T
}

export const readBootstrap = (signal?: AbortSignal, baseUrl = '') => request<{ review_enabled: boolean }>('/v1/setup/bootstrap', '', 'GET', undefined, signal, baseUrl)
export const readSetup = (token: ReviewAccess, signal?: AbortSignal, baseUrl = '') => request<SetupStatus>('/v1/ui/setup', token, 'GET', undefined, signal, baseUrl)
export const saveModel = (token: ReviewAccess, settings: ModelSettings, baseUrl = '') => request<ModelStatus>('/v1/ui/setup/model', token, 'PUT', settings, undefined, baseUrl)
export const importRecords = (token: ReviewAccess, payload: ImportPayload, baseUrl = '') => request<ImportResult>('/v1/ui/import', token, 'POST', payload, undefined, baseUrl)
