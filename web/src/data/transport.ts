import type { ReviewAccess } from './auth'

/** A connection points at an API root, optionally behind a reverse-proxy path. */
export function normalizeEngineUrl(value: string): string {
  let url: URL
  try { url = new URL(value.trim()) }
  catch { throw new Error('Enter the full engine URL, such as https://respawned.example.com.') }
  const loopback = url.hostname === 'localhost' || url.hostname === '[::1]' || /^127\.(?:\d{1,3}\.){2}\d{1,3}$/.test(url.hostname)
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) throw new Error('Use HTTPS for a remote engine. HTTP is allowed only for a loopback address on this computer.')
  if (url.port === '0') throw new Error('Use an engine port from 1 to 65535.')
  if (url.username || url.password || url.search || url.hash) throw new Error('Use an engine URL without a username, password, query, or fragment.')
  return `${url.origin}${url.pathname.replace(/\/+$/, '')}`
}

export function engineRequestOptions(access: ReviewAccess, baseUrl = ''): RequestInit {
  if (baseUrl && typeof access === 'object' && access?.mode === 'session') throw new Error('Remote engines require their own access token. Your local browser session cannot be forwarded.')
  if (baseUrl) normalizeEngineUrl(baseUrl)
  return { credentials: baseUrl ? 'omit' : 'same-origin', redirect: 'error' }
}

export function engineNetworkError(baseUrl = ''): string {
  return baseUrl
    ? 'Could not reach this engine. Check its URL, HTTPS certificate, and network access. On that server, allow this browser origin with RESPAWNED_UI_ORIGINS, then restart it.'
    : 'Could not reach Respawned. Check that the server is running and try again.'
}
