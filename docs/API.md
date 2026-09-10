# API reference

Respawned exposes one deterministic application core through the CLI and HTTP
API. A source adapter imports facts; policy selects candidates; a drafting
backend writes copy; review reserves an unsent outbox message. See the running
server's `/docs` and `/openapi.json` for complete request and response schemas.
The [browser guide](WEB_UI.md) covers operator setup and workspace monitoring.

## API access

| Operation | Access | Purpose |
| --- | --- | --- |
| `GET /healthz`, `GET /readyz` | None | Process liveness; database/schema/policy readiness |
| `POST /v1/ingest` | Trusted network | Import canonical source facts |
| `GET /v1/inbox`, `/v1/drafts`, `/v1/outbox` | Trusted network | Read correspondence, draft history, and reservations |
| `POST /v1/process` | `RESPAWNED_PROCESS_TOKEN` | Draft a bounded queue under server policy |
| `/v1/ui/*` review, settings, import, workspace, and overview operations | Local browser session or `RESPAWNED_REVIEW_TOKEN` | The API used by the bundled UI |
| `GET /v1/outbox/export` | Local browser session or `RESPAWNED_REVIEW_TOKEN` | Export the same JSON/CSV as `/v1/ui/outbox/export` |

Session/bootstrap discovery endpoints expose only access availability. Review
credentials are independent of the processing token. A draft's `review_token`
is a version fingerprint, not an access credential. See [browser access](WEB_UI.md#server-and-api-access)
for local launch sessions, manual Bearer access, and token generation.

Legacy ingestion and read routes have no built-in authentication or tenant
isolation. Use loopback, a trusted network, or an authenticated reverse proxy.
Enabling browser CORS does not secure those routes. Remote UI connections also
need the server's [exact-origin allowlist](WEB_UI.md#add-an-engine-connection).

## Ingest records

Use **Setup → Records & sources** or submit a JSON file to the canonical API:

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/ingest \
  -H 'Content-Type: application/json' --data-binary @records.json
```

The file contains `opportunities` and `activities` arrays. The authenticated UI
alias, `POST /v1/ui/import`, accepts at most 1,000 combined items and 2 MB. Import
does not synchronize candidates, call a model, or send a message. For example:

```json
{
  "opportunities": [{
    "id": "ats:application-123",
    "kind": "job_application",
    "title": "Backend Engineer at Example",
    "status": "open",
    "created_at": "2026-09-01T15:00:00Z",
    "context": {
      "company": "Example",
      "role": "Backend Engineer",
      "stage": "Applied",
      "summary": "Application received; no human contact is known.",
      "source_url": "https://example.com/applications/123"
    }
  }],
  "activities": [{
    "id": "mail:receipt-456",
    "opportunity_id": "ats:application-123",
    "type": "application_receipt",
    "occurred_at": "2026-09-01T15:01:00Z",
    "channel": "email",
    "direction": "inbound",
    "classification": "automated",
    "summary": "Automated application confirmation."
  }]
}
```

Replace example identities, dates, and facts with source data. This record is
trackable without a contact and cannot become an outreach candidate. Once a
recipient is confirmed, import a complete replacement snapshot containing its
stable `contact_key` and at least one of `contact_email` or `contact_phone`.
Identity is separate from destination: addresses can change or be shared.
Never infer a human recipient from a no-reply receipt address.

### Field rules

| Field | Contract |
| --- | --- |
| Opportunity `id`, `status`, `created_at` | Required; status is `open`, `won`, or `lost`; timestamps need a UTC offset |
| `contact_key` and contact routes | Both present together, or both absent for tracking-only records |
| `preferred_channel` | Optional `email` or `sms`; requires a contact route |
| `contact_name`, `owner_name`, `last_contact_at` | Optional source facts; dates need a UTC offset |
| `kind`, `title` | Kind defaults to `generic`, matches `[a-z][a-z0-9_]{0,63}`; optional title is at most 300 characters |
| `value` | Optional nonnegative decimal; at most ten integer and two fractional digits; values requiring rounding are rejected |
| `context` | Only `company` (200 characters), `role` (200), `stage` (100), `summary` (2,000), `expected_reply_at` (aware timestamp), `source_url` (2,048) |
| Activity `id`, `opportunity_id`, `type`, `occurred_at` | Required stable identity, referenced record, event type, and aware timestamp |
| Activity `channel`, `direction`, `classification` | Optional channel; `inbound` or `outbound`; `human`, `automated`, or `unknown` (default) |
| Activity `summary`, `source_url` | Optional, bounded to 2,000 and 2,048 characters respectively |

Supplied text fields must be nonempty. Source URLs must use HTTP(S), with no
embedded credentials. Extra fields are rejected. HTTP and in-process adapters
share [the same validated contract](../src/respawned/core/contracts.py).
Two roles at one company remain separate records; labels do not merge identities.

The built-in policy recognizes `opportunity_created`, `content_viewed`,
`contact_replied`, `message_sent`, `opportunity_won`, and `opportunity_lost`.
Explicitly human inbound events may use a source-specific type. Legacy
unclassified `contact_replied` events retain reply semantics, but an explicitly
automated event never counts as a human response. The reply inbox requires
`direction: inbound`. Record actual sent messages as `message_sent` with
`direction: outbound` and the source timestamp.

### Replay and ordering

- Opportunities upsert by `id` as **complete snapshots**, not patches. Omitted
  optional fields are cleared. Send every field that should remain stored.
- Activities are immutable. Exact replay is idempotent; an existing ID with
  changed content returns HTTP 409, including changed nullable fields.
- An activity whose opportunity is missing returns HTTP 409. Send its record
  first, then retry the activity. Conflicts roll back the entire import batch.
- The API acknowledges success after commit. If a transport failure leaves the
  outcome unknown, retry the same IDs and payloads instead of inventing new IDs.
- Opportunity snapshots have no source revision check. Serialize source updates
  so an older snapshot cannot overwrite newer facts.

Source credentials, account namespaces, pagination, cursors, mapping, rate
limits, and scheduling belong to the connector. The engine does not query a
customer's mailbox or database. Source freshness remains unknown until a future
integration establishes it.

## Candidate and review workflow

Use the installed CLI with the same database environment as the server. The
[installation guide](../README.md#install-and-start) covers the website
downloads and PostgreSQL setup; no source checkout is needed for the packaged CLI.

```bash
respawned sync --dry-run
respawned sync --limit 10
respawned review
```

The CLI connects directly to PostgreSQL and does not load `.env` automatically.
For a developer checkout, prefix these commands with `uv run --env-file .env`.

Dry-run sync does not write candidates or a sync run. Real sync publishes up to
ten eligible contact groups by default. Reviewed history and active outbox
cooldowns are filtered before the limit; syncing after reviewing the first batch
exposes subsequent eligible contacts. Unchanged evidence reuses candidate identity.
Rejection suppresses that identity until relevant evidence, reason, route, or
referenced records change. Pending drafts remain available.

CLI review offers **approve**, **reject**, **edit**, and **skip**; Enter skips.
The browser offers the same human review operations. New copy is generated on
demand, then validated by the engine. Reviewing an existing pending draft or an
empty queue does not need model credentials. Approval checks the displayed draft version,
recipient, current referenced records, and contact-wide cooldown before reserving
one outbox row. Stale edits or approvals must reopen the current version.
In-process callers pass `expected_review_token=draft.review_token`.

Browser drafting of one selected record evaluates the whole engine but persists
only that selection. It preserves the CLI's published queue. Workspaces likewise
filter presentation without weakening cross-workspace contact grouping or cooldowns.
The [shared-service map](WEB_UI.md#shared-application-boundary) lists UI/API/CLI entry points.

Use `--policy path/to/policy.yaml` with `sync`, `review`, or `inbox`. A server uses
`RESPAWNED_POLICY_PATH`; the UI displays policy but does not edit it. For synthetic
fixtures only, `--now <timestamp-with-offset>` freezes evaluation. It limits visible
activities but cannot reconstruct older mutable record snapshots. Future outbox
reservations conservatively block backdated evaluation. Normal operation should
omit `--now`.

## Processing and automatic authorization

The bundled [policy](../src/respawned/config/policy.yaml) defaults to:

```yaml
review:
  mode: human
```

Changing `mode` to `automatic` explicitly permits policy-controlled authorization
without a human prompt. Both modes apply the same validation, version, recipient,
state, cooldown, and uniqueness checks. Neither sends messages.

| Policy mode | Processing result | Outbox provenance |
| --- | --- | --- |
| `human` | Pending draft, awaiting review | Human approval records `human` and a review time |
| `automatic` | Eligible, valid draft becomes an unsent reservation | Records `automatic`; human review time stays null |

Explicit browser approval remains a human action under either processing policy.
Changing back to human mode affects later processing, not prior reservations or
an already running batch. Initialization labels older reservations
`legacy_unknown` instead of inventing approval history.

`POST /v1/process` is disabled with HTTP 404 until `RESPAWNED_PROCESS_TOKEN` is
configured on the server. With that token already in your shell environment:

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/process \
  -H "Authorization: Bearer $RESPAWNED_PROCESS_TOKEN" \
  -H 'Content-Type: application/json' -d '{"limit": 10}'
```

The only input is `limit`, an integer from 1 to 50. The server controls time,
policy, and authorization actor; source payloads cannot override them. A request
without the credential returns 401 before database/model setup.

Inspect every per-item outcome (`pending`, `authorized`, `blocked`, or
`already_reviewed`); HTTP 200 does not mean each item succeeded. Expected model
failures block individual items. Completed steps commit independently, so a
later failure can leave earlier progress. Retrying reuses persisted work.

## Inbox and outbox

`respawned inbox --json` and `GET /v1/inbox` show contact/channel groups with human
replies and no later ingested outbound, even during outreach cooldown. Pending
outbox reservations do not count as sent answers. The inbox applies closed/expired
record exclusions and exposes evidence IDs, routes, reply time, and pending outbox
counts. It reports `total` and `has_more`; `limit` is 1–200 (default 50), with no
offset pagination. Its `now` query, like CLI `--now`, only limits activity visibility.

`GET /v1/drafts` and `/v1/outbox` accept `limit` (1–200, default 50) and `offset`.
These are snapshot reads, not synchronization cursors or worker claims. Reconcile
stable IDs if concurrent writes shift pages.

`respawned outbox --path outbox.csv` and the protected
`GET /v1/outbox/export?format=csv` share the same 12-field formatter. Use
`format=json` for exact original strings; CSV prefixes formula-like cells for
spreadsheet safety and uses UTC timestamps. An optional `workspace_id` filters
whole reservations referencing matching records. Export is read-only.

Approval reserves the reviewed copy and recipient. It does not send or mark the
message sent. An actual outbound activity updates inbox/cooldown state but cannot
identify a delivered reservation without explicit provider-message correlation.
The [manual outbox workflow](WEB_UI.md#import-records-and-use-the-outbox) remains
the supported path; a delivery worker would need claims, retries, cancellation,
freshness checks, and correlated delivery results.

## Policy and drafting context

Policy defines business timezone, cooldown, expiry, sender/sign-off, copy limits,
reason base scores, and ranking weights. Candidate ranking combines the strongest
reason with normalized value and signal weights. The strongest reason supplies
the explanation and tone; value does not change that explanation.

The default rules include unanswered human replies, repeated content views,
viewed-without-reply delay, quiet high-value work, aging work, applications with
no human update after seven calendar days, and missed expected replies after one
day of grace. Job applications are excluded from value ranking. A newer human
reply or outbound suppresses an older promised-update deadline.

The optional `awaiting_reply` evaluator is disabled by default. To enable it,
add a reason while retaining the rest of a valid policy:

```yaml
reasons:
  awaiting_reply:
    evaluator: awaiting_reply
    base_score: 40
    tone: courteous and concise
    params:
      minimum_days: 7
      horizon_days: 14
```

New rules using existing evaluators need only policy entries. A new signal needs
an evaluator factory and registry entry; core review and outbox behavior stays
shared. See [policy loading](../src/respawned/core/policy.py) and
[evaluators](../src/respawned/core/reasons.py).

Drafting receives the selected record's kind, title, company, role, stage, and
bounded summary with contact/tone/sender context. Raw activity feeds, source URLs,
expected timestamps, and monetary values are excluded. The engine validates both
generated and edited copy for length, unresolved placeholders, prohibited currency
amounts, and referenced-record eligibility. Facts remain data, not instructions.
Configure [a drafting backend](WEB_UI.md#connect-a-model-backend) on the engine.
Saving settings validates configuration without probing a runtime, login, or provider.
