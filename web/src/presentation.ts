import type { UIRecord } from './data/types'

export type Page = 'Overview' | 'Connections' | 'Review queue' | 'Reply inbox' | 'Outbox' | 'Activity' | 'Policy' | 'Workspaces' | 'Setup'
export const contextNames: Record<string, string> = {
  job_application: 'Job applications', sales: 'Sales', generic: 'Other work',
  music_submission: 'Music submissions', music: 'Music submissions',
}
export const actionable = (record: UIRecord) =>
  ['reply', 'follow_up'].includes(record.next_action) && !!record.contact && !!record.candidate_id
export const shortDate = (date: string | null) => date
  ? new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(date))
  : 'Unknown'
export const initials = (name: string) => name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase()
export const displayName = (record: UIRecord) => record.contact?.name || record.title || 'Untitled record'
export function contextLine(record: UIRecord) {
  const company = record.fields.find(field => field.label.toLowerCase() === 'company')?.value
  const role = record.fields.find(field => field.label.toLowerCase() === 'role')?.value
  if (company || role) return [company, role].filter(Boolean).join(' · ')
  return record.title
}
export function actionLabel(record: UIRecord) {
  if (record.next_action === 'blocked') return record.reason.code === 'missing_contact' ? 'Contact needed' : 'Needs review'
  return ({ reply: 'Reply needed', follow_up: 'Follow up', waiting: 'Waiting',
    blocked: 'Contact needed', closed: 'Closed', approved: 'In outbox', rejected: 'Rejected' })[record.next_action]
}
export function safeSource(url: string | null | undefined): string | undefined {
  if (!url) return undefined
  try { const parsed = new URL(url); return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : undefined }
  catch { return undefined }
}
