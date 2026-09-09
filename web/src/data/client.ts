import type { InboxResult, OutboxItem, RecordPage, ReviewClient, SyncResult, UIConfig, UIDraft, UIRecord } from './types'

export class ClientError extends Error {
  constructor(
    message: string,
    public readonly status = 0,
    public readonly code = 'request_failed',
    public readonly validation_errors: string[] = [],
  ) {
    super(message)
    this.name = 'ClientError'
  }
}

/** Counts Unicode code points, matching Python's length check on the server. */
export function validateDraft(body: string, maximum = 320): string[] {
  if (!body.trim()) return ['Write a message before saving or approving.']
  if (Array.from(body.trim()).length > maximum) return [`Keep the message to ${maximum} characters or fewer.`]
  return []
}

function errorMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== 'object') return fallback
  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item: unknown) => {
      if (item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string') return [item.msg]
      return []
    })
    if (messages.length) return messages.join(' ')
  }
  return fallback
}

/** Credentials live only in this closure; they are never written to storage or URLs. */
export function createHttpClient(baseUrl = '', operatorToken = '', workspaceId = ''): ReviewClient {
  const root = `${baseUrl.replace(/\/+$/, '')}/v1/ui`
  async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (operatorToken) headers.Authorization = `Bearer ${operatorToken}`
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    let response: Response
    try {
      response = await fetch(`${root}${path}`, { method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    } catch {
      throw new ClientError('Could not reach the review API. Check the connection and try again.', 0, 'network_error')
    }
    let payload: unknown
    try {
      payload = await response.json()
    } catch {
      throw new ClientError('The review API returned an unreadable response.', response.status, 'invalid_response')
    }
    if (!response.ok) {
      const fallback = response.status === 401 || response.status === 403
        ? 'The operator token was not accepted. Check the connection settings.'
        : response.status === 409
          ? 'The record or draft changed. Refresh and review the latest copy.'
          : 'The request could not be completed. Please try again.'
      throw new ClientError(errorMessage(payload, fallback), response.status,
        response.status === 409 ? 'stale_review' : response.status === 422 ? 'validation_error' : 'request_failed')
    }
    return payload as T
  }
  return {
    mode: 'live',
    config: () => request<UIConfig>('/config'),
    listRecords: (offset = 0) => request<RecordPage>(`/records?limit=50&offset=${Math.max(0, Math.floor(offset))}${workspaceId ? `&workspace_id=${encodeURIComponent(workspaceId)}` : ''}`),
    getRecord: (recordId) => request<UIRecord>(`/records/${encodeURIComponent(recordId)}`),
    sync: () => request<SyncResult>('/sync', 'POST', {}),
    draft: (recordId) => request<UIDraft>(`/records/${encodeURIComponent(recordId)}/draft`, 'POST'),
    edit: (draftId, body, reviewToken) => request<UIDraft>(`/drafts/${encodeURIComponent(draftId)}/edit`, 'POST', { body, review_token: reviewToken }),
    approve: (draftId, reviewToken) => request<UIDraft>(`/drafts/${encodeURIComponent(draftId)}/approve`, 'POST', { review_token: reviewToken }),
    reject: (draftId, reviewToken) => request<UIDraft>(`/drafts/${encodeURIComponent(draftId)}/reject`, 'POST', { review_token: reviewToken }),
    listInbox: () => request<InboxResult>(`/inbox?limit=200${workspaceId ? `&workspace_id=${encodeURIComponent(workspaceId)}` : ''}`),
    async listOutbox() {
      const items: OutboxItem[] = []
      for (;;) {
        const page = await request<{ items: OutboxItem[]; has_more: boolean }>(`/outbox?limit=100&offset=${items.length}`)
        items.push(...page.items)
        if (!page.has_more || !page.items.length) return items
      }
    },
  }
}
