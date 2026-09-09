import type { UIActivity, UIConfig, UIRecord } from './types'

/** Synthetic examples, frozen so a browser's clock never changes eligibility. */
export const DEMO_AS_OF = '2026-09-09T14:00:00Z'
export const DEMO_STORAGE_KEY = 'respawned:synthetic-demo:v3'
export const DEMO_CONFIG: UIConfig = {
  policy_mode: 'human',
  cooldown_hours: 48,
  max_draft_characters: 320,
  source_freshness: 'demo',
}

function activity(
  id: string,
  occurred_at: string,
  label: string,
  summary: string,
  type = 'inbound',
  classification: UIActivity['classification'] = 'human',
): UIActivity {
  return { id, occurred_at, label, summary, type, classification, source_url: null }
}

function contact(name: string, slug: string) {
  return { key: `demo:${slug}`, name, address: `${slug}@example.com`, channel: 'email' as const }
}

type FixtureInput = Omit<UIRecord, 'status' | 'score' | 'candidate_id' | 'referenced_record_ids' | 'source_freshness'> & {
  status?: string
  score?: number | null
  referenced_record_ids?: string[]
}

function record(input: FixtureInput): UIRecord {
  const actionable = input.next_action === 'reply' || input.next_action === 'follow_up'
  return {
    status: 'open',
    score: actionable ? 60 : null,
    candidate_id: actionable ? `candidate:${input.id}` : null,
    referenced_record_ids: [],
    source_freshness: 'demo',
    ...input,
  }
}

const fixtures: UIRecord[] = [
  record({
    id: 'job-northstar-backend', kind: 'job_application', title: 'Backend Engineer', stage: 'Interviewing',
    contact: { ...contact('Maya Chen', 'maya.chen'), address: 'maya@northstar.example' },
    fields: [{ label: 'Company', value: 'Northstar Labs' }, { label: 'Role', value: 'Backend Engineer' }],
    reason: { code: 'commitment_overdue', label: 'Update overdue', detail: 'An update was expected September 5. No later human reply is recorded.' },
    next_action: 'follow_up', score: 96, last_contact_at: '2026-09-02T16:30:00Z',
    activities: [
      activity('maya-3', '2026-09-02T16:30:00Z', 'Screening completed', 'Completed the screening and sent a thank-you. An update was expected by September 5.', 'outbound'),
      activity('maya-2', '2026-08-26T18:00:00Z', 'Recruiter replied', 'Maya replied to arrange a screening for the Backend Engineer role and expected to share an update by September 5.'),
      activity('maya-1', '2026-08-22T13:15:00Z', 'Application confirmed', 'Northstar confirmed receipt of the Backend Engineer application.', 'inbound', 'automated'),
    ],
    draft: {
      id: 'draft:job-northstar-backend',
      body: 'Hi Maya,\n\nThanks again for discussing the Backend Engineer role. I wanted to check whether there are any updates on next steps.\n\nBest,\nWilliam',
      status: 'pending', review_token: 'demo:job-northstar-backend:1', validation_errors: [], outbox_id: null,
    },
  }),
  record({
    id: 'job-aster-platform', kind: 'job_application', title: 'Platform Engineer', stage: 'Interviewing',
    contact: contact('Jordan Ellis', 'jordan.ellis'),
    fields: [{ label: 'Company', value: 'Aster Health' }, { label: 'Role', value: 'Platform Engineer' }],
    reason: { code: 'needs_reply', label: 'Reply needed', detail: 'Jordan asked whether you would like to continue to the next interview round.' },
    next_action: 'reply', score: 91, last_contact_at: '2026-09-08T15:20:00Z',
    activities: [
      activity('jordan-2', '2026-09-08T15:20:00Z', 'Jordan asked about next steps', 'The team enjoyed your conversation. Would you like to continue to the next interview round?'),
      activity('jordan-1', '2026-09-01T17:00:00Z', 'Recruiter conversation', 'Discussed the Platform Engineer position.', 'outbound'),
      activity('jordan-application', '2026-08-22T12:00:00Z', 'Application confirmed', 'Aster Health confirmed receipt of the Platform Engineer application.', 'inbound', 'automated'),
    ],
  }),
  record({
    id: 'job-civic-product', kind: 'job_application', title: 'Software Engineer', stage: 'Applied',
    contact: contact('Sam Rivera', 'sam.rivera'),
    fields: [{ label: 'Company', value: 'Civic Systems' }, { label: 'Role', value: 'Software Engineer' }],
    reason: { code: 'idle', label: 'Follow-up due', detail: 'Your August 31 message has no recorded human reply. The automated receipt is not a reply.' },
    next_action: 'follow_up', score: 88, last_contact_at: '2026-08-31T14:40:00Z',
    activities: [
      activity('sam-3', '2026-09-01T09:00:00Z', 'Automatic acknowledgment', 'Your message has been received by the recruiting inbox.', 'inbound', 'automated'),
      activity('sam-2', '2026-08-31T14:40:00Z', 'Application introduction', 'Sent Sam a short introduction about the Software Engineer application.', 'outbound'),
      activity('sam-1', '2026-08-26T10:00:00Z', 'Application submitted', 'Applied for the Software Engineer role at Civic Systems.', 'application', 'unknown'),
    ],
  }),
  record({
    id: 'job-meridian-design', kind: 'job_application', title: 'Frontend Engineer', stage: 'Screening',
    contact: contact('Taylor Brooks', 'taylor.brooks'),
    fields: [{ label: 'Company', value: 'Meridian Studio' }, { label: 'Role', value: 'Frontend Engineer' }],
    reason: { code: 'needs_reply', label: 'Reply needed', detail: 'Taylor asked for a portfolio link. Review the draft and add the correct link before approval.' },
    next_action: 'reply', score: 84, last_contact_at: '2026-09-08T18:10:00Z',
    activities: [
      activity('taylor-1', '2026-09-08T18:10:00Z', 'Portfolio requested', 'Could you share a link to your portfolio before we arrange the next conversation?'),
      activity('taylor-application', '2026-08-29T12:00:00Z', 'Application confirmed', 'Meridian Studio confirmed receipt of the Frontend Engineer application.', 'inbound', 'automated'),
    ],
  }),
  record({
    id: 'job-atlas-software', kind: 'job_application', title: 'ML Engineer', stage: 'Interviewing',
    contact: contact('Riley Patel', 'riley.patel'),
    fields: [{ label: 'Company', value: 'Atlas Research' }, { label: 'Role', value: 'ML Engineer' }],
    reason: { code: 'idle', label: 'Follow-up due', detail: 'The last recorded exchange was on August 28, after the first interview.' },
    next_action: 'follow_up', score: 72, last_contact_at: '2026-08-28T12:00:00Z',
    activities: [
      activity('riley-1', '2026-08-28T12:00:00Z', 'Interview follow-up', 'Thanks for the conversation. We will be in touch when we have an update.'),
      activity('riley-application', '2026-08-12T12:00:00Z', 'Application confirmed', 'Atlas Research confirmed receipt of the ML Engineer application.', 'inbound', 'automated'),
    ],
  }),
  record({
    id: 'job-northstar-platform', kind: 'job_application', title: 'Developer Platform Engineer', stage: 'Applied', contact: null,
    fields: [{ label: 'Company', value: 'Northstar Labs' }, { label: 'Role', value: 'Developer Platform Engineer' }],
    reason: { code: 'missing_contact', label: 'Contact needed', detail: 'Only an automated receipt is recorded. A confirmed human recipient is needed before this application can receive a follow-up.' },
    next_action: 'blocked', last_contact_at: null,
    activities: [activity('northstar-platform-1', '2026-08-20T13:00:00Z', 'Application received', 'Application acknowledgment for the Developer Platform Engineer role.', 'inbound', 'automated')],
  }),
  record({
    id: 'job-harbor-infra', kind: 'job_application', title: 'Infrastructure Engineer', stage: 'Interviewing',
    contact: contact('Casey Kim', 'casey.kim'),
    fields: [{ label: 'Company', value: 'Harbor' }, { label: 'Role', value: 'Infrastructure Engineer' }],
    reason: { code: 'waiting', label: 'Waiting until Sep 11', detail: 'Casey committed to an update by September 11. That date has not arrived in this demo.' },
    next_action: 'waiting', last_contact_at: '2026-09-08T16:00:00Z',
    activities: [activity('casey-1', '2026-09-08T16:00:00Z', 'Update expected Friday', 'I will share an update by September 11.')],
  }),
  record({
    id: 'job-cedar-engineer', kind: 'job_application', title: 'Full Stack Engineer', stage: 'Closed', status: 'closed',
    contact: contact('Drew Nguyen', 'drew.nguyen'),
    fields: [{ label: 'Company', value: 'Cedar' }, { label: 'Role', value: 'Full Stack Engineer' }],
    reason: { code: 'closed', label: 'Application closed', detail: 'The application was closed on September 3. No follow-up is eligible.' },
    next_action: 'closed', last_contact_at: '2026-09-03T11:00:00Z',
    activities: [activity('drew-1', '2026-09-03T11:00:00Z', 'Application closed', 'The team has filled the role and closed the application.', 'closed')],
  }),
  record({
    id: 'sales-polaris-renewal', kind: 'sales', title: 'Workspace renewal', stage: 'Proposal',
    contact: contact('Alex Morgan', 'alex.morgan'),
    fields: [{ label: 'Value', value: '$12,000' }, { label: 'Owner', value: 'Jordan Lee' }],
    reason: { code: 'commitment_overdue', label: 'Decision overdue', detail: 'Alex expected feedback on the renewal proposal by September 4.' },
    next_action: 'follow_up', score: 94, last_contact_at: '2026-09-01T14:00:00Z',
    referenced_record_ids: ['sales-polaris-analytics'],
    activities: [
      activity('alex-2', '2026-09-01T14:00:00Z', 'Alex shared a review date', 'We are reviewing the renewal proposal and expect to have feedback by September 4.'),
      activity('alex-1', '2026-08-27T10:00:00Z', 'Renewal proposal shared', 'Sent the workspace renewal proposal for review.', 'outbound'),
    ],
  }),
  record({
    id: 'sales-orbit-pilot', kind: 'sales', title: 'Team pilot', stage: 'Discovery',
    contact: contact('Avery Stone', 'avery.stone'),
    fields: [{ label: 'Company', value: 'Orbit' }, { label: 'Opportunity', value: 'Team pilot' }],
    reason: { code: 'needs_reply', label: 'Reply needed', detail: 'Avery asked which materials would help evaluate a team pilot. No value is recorded for this opportunity.' },
    next_action: 'reply', score: 89, last_contact_at: '2026-09-08T13:00:00Z',
    activities: [activity('avery-1', '2026-09-08T13:00:00Z', 'Pilot information requested', 'What information would help us evaluate a pilot with the team?')],
  }),
  record({
    id: 'sales-canopy-expansion', kind: 'sales', title: 'Team expansion', stage: 'Negotiation',
    contact: contact('Chris Park', 'chris.park'),
    fields: [{ label: 'Company', value: 'Canopy' }, { label: 'Opportunity', value: 'Team expansion' }, { label: 'Value', value: '$8,400' }],
    reason: { code: 'idle', label: 'Follow-up due', detail: 'No human response is recorded after the August 31 proposal discussion.' },
    next_action: 'follow_up', score: 74, last_contact_at: '2026-08-31T15:00:00Z',
    activities: [activity('chris-1', '2026-08-31T15:00:00Z', 'Expansion details sent', 'Shared the requested expansion details.', 'outbound')],
  }),
  record({
    id: 'sales-fieldwork-onboarding', kind: 'sales', title: 'Onboarding workshop', stage: 'Proposal',
    contact: contact('Devon Reed', 'devon.reed'),
    fields: [{ label: 'Company', value: 'Fieldwork' }, { label: 'Opportunity', value: 'Onboarding workshop' }, { label: 'Value', value: '$3,600' }],
    reason: { code: 'cooldown', label: 'Recently contacted', detail: 'An outbound message was recorded on September 8. The contact is inside the 48-hour cooldown.' },
    next_action: 'waiting', last_contact_at: '2026-09-08T15:00:00Z',
    activities: [
      activity('devon-2', '2026-09-09T10:00:00Z', 'Devon asked about preparation', 'Thanks for the outline. What should the team prepare before the workshop?'),
      activity('devon-1', '2026-09-08T15:00:00Z', 'Workshop outline shared', 'Sent Devon the workshop outline.', 'outbound'),
    ],
  }),
  record({
    id: 'sales-polaris-analytics', kind: 'sales', title: 'Analytics add-on', stage: 'Discovery',
    contact: contact('Alex Morgan', 'alex.morgan'),
    fields: [{ label: 'Company', value: 'Polaris' }, { label: 'Opportunity', value: 'Analytics add-on' }],
    reason: { code: 'related_record', label: 'Review with renewal', detail: 'Shares Alex’s confirmed contact with the workspace renewal. Review the renewal’s single candidate; this related record does not create a separate follow-up.' },
    next_action: 'waiting', last_contact_at: '2026-08-28T15:00:00Z',
    referenced_record_ids: ['sales-polaris-renewal'],
    activities: [activity('alex-analytics-1', '2026-08-28T15:00:00Z', 'Analytics discussion', 'Alex asked to keep the analytics add-on in mind during renewal discussions.')],
  }),
  record({
    id: 'music-lantern-booking', kind: 'music', title: 'Autumn showcase', stage: 'In discussion',
    contact: contact('Jamie Quinn', 'jamie.quinn'),
    fields: [{ label: 'Venue', value: 'The Lantern Room' }, { label: 'Event', value: 'Autumn showcase' }, { label: 'Project', value: 'Live set' }],
    reason: { code: 'idle', label: 'Follow-up due', detail: 'Jamie has not replied to the September 1 booking inquiry. No performance date or fee is confirmed.' },
    next_action: 'follow_up', score: 67, last_contact_at: '2026-09-01T12:00:00Z',
    activities: [activity('jamie-1', '2026-09-01T12:00:00Z', 'Booking inquiry sent', 'Asked Jamie about the autumn showcase and the venue’s booking process.', 'outbound')],
  }),
  record({
    id: 'generic-community-session', kind: 'community_partnership', title: 'Neighborhood skill-share', stage: 'Planning',
    contact: contact('Robin Bell', 'robin.bell'),
    fields: [{ label: 'Organization', value: 'Common Ground' }, { label: 'Topic', value: 'Neighborhood skill-share' }, { label: 'Format', value: 'Community workshop' }],
    reason: { code: 'needs_reply', label: 'Reply needed', detail: 'Robin asked which workshop topic you want to explore. This custom record uses the same review flow.' },
    next_action: 'reply', score: 81, last_contact_at: '2026-09-07T14:00:00Z',
    activities: [activity('robin-1', '2026-09-07T14:00:00Z', 'Workshop topic requested', 'We would be interested in a skill-share. Which topic would you like to explore?')],
  }),
]

/** Every returned example is independent; callers may safely mutate their copy. */
export function createFixtures(): UIRecord[] {
  return structuredClone(fixtures)
}

export const DEMO_DRAFT_BODIES: Record<string, string> = {
  'job-aster-platform': 'Hi Jordan, thank you for the update. I appreciate the team taking the time to speak with me. Could you share what the next interview round involves?',
  'job-civic-product': 'Hi Sam, I wanted to follow up on my Software Engineer application at Civic Systems. Is there an update on the hiring process or anything else I can provide?',
  'job-meridian-design': 'Hi Taylor, thanks for reaching out. Is there a particular type of project you would like me to highlight when I share my portfolio?',
  'job-atlas-software': 'Hi Riley, thank you again for the conversation about the ML Engineer role at Atlas Research. I wanted to check whether there are any updates on next steps.',
  'sales-polaris-renewal': 'Hi Alex, following up on the workspace renewal proposal. You mentioned feedback by September 4. Has the team had a chance to review it, and are there any questions I can help with?',
  'sales-polaris-analytics': 'Hi Alex, I wanted to check in on the analytics add-on alongside the workspace renewal. Are there any questions about the options discussed?',
  'sales-orbit-pilot': 'Hi Avery, thanks for your interest in a team pilot. Could you share the main questions your team wants to evaluate so I can put together the most relevant information?',
  'sales-canopy-expansion': 'Hi Chris, following up on the team expansion details I shared. Has the team had a chance to review them, or is there anything else that would help?',
  'music-lantern-booking': 'Hi Jamie, I wanted to follow up on my inquiry about the autumn showcase at The Lantern Room. Could you share whether you are still considering acts and what the next steps would be?',
  'generic-community-session': 'Hi Robin, thanks for your interest in a neighborhood skill-share. Could you share which topics the community is most interested in so we can find a useful fit?',
}
