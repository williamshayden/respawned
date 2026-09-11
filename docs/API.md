# API reference

The browser, CLI, and Python client call the same HTTP API. The engine stores records, evaluates policy, validates drafts, and records review and delivery results.

Start with [Agent integration](AGENT_INTEGRATION.md). The running engine serves interactive schemas at `/docs` and machine-readable schemas at `/openapi.json`.

## API access

Since Respawned 2.0, data and workflow routes require authentication. Use HTTPS for remote connections.

| Credential | Access |
| --- | --- |
| Local browser session or private local CLI access | Operator workflow on that engine |
| `RESPAWNED_REVIEW_TOKEN` | Operator access, including import, settings, drafting, review, processing, and outbox |
| `RESPAWNED_PROCESS_TOKEN` | Optional processing-only access to `POST /v1/process` |
| `RESPAWNED_OUTBOX_TOKEN` | Approved outbox reads and confirmed-send receipts |

HTTP clients send `Authorization: Bearer <credential>`. Set secrets in the server environment and restart after changing them. Client credentials belong in environment variables or private configuration, never in source files or prompts.

`respawned ui` creates local browser and CLI access automatically. Remote CLI and SDK clients use `RESPAWNED_API_URL` and `RESPAWNED_REVIEW_TOKEN`. See [engine access](WEB_UI.md#server-and-api-access) for setup.

`/healthz`, `/readyz`, schema routes, and session/bootstrap discovery expose no record data. Starting with 2.1, `/healthz` and `GET /v1/setup/bootstrap` report `engine_version` from the installed package. Bootstrap also advertises `workflow_api_prefix: "/v1/workflow"`. New clients use this canonical prefix; `/v1/ui` remains a compatibility alias.

A draft's `review_token` identifies the version being reviewed. It is not an access credential.

## Engine status and versions

`respawned --version` reports the installed client version. With an engine running, inspect its version and readiness:

```bash
respawned status
respawned status --json
```

Status reads public `GET /healthz` and `GET /readyz` without sending an access credential. The client needs no database settings and invokes no model. `/healthz` checks process liveness; `/readyz` checks the engine's database, schema, and policy. A successful status check does not establish workflow authorization.

Set `RESPAWNED_API_URL` or use `respawned status --api-url https://engine.example.com --timeout 5` to check another engine. The timeout applies separately to each HTTP request; requests are not retried.

The report includes client and engine versions, readiness, and major-version compatibility. Different minor or patch versions within the same major are displayed and accepted by the status check. The qualified client/SDK and engine pair for this release is 2.1.0. `/v1` identifies the HTTP route contract and is separate from the package version.

The `--json` output and SDK return dictionary contain:

| Field | Value |
| --- | --- |
| `api_url` | Normalized engine URL |
| `client_version`, `engine_version` | Package versions; engine version is null when unavailable or invalid |
| `compatibility` | `same_major`, `different_major`, or `unknown` |
| `compatibility_detail` | Explanation for the version result |
| `health.status` | `ok`, `error`, or `unreachable` |
| `readiness.status` | `ready`, `not_ready`, or `not_checked` when health failed |
| `health.detail`, `readiness.detail` | Error explanation or null |
| `workflow_access` | `not_checked` |

| CLI exit code | Meaning |
| --- | --- |
| 0 | Engine is ready and the client and engine major versions match |
| 1 | Engine is unreachable or not ready |
| 3 | Client and engine major versions differ |
| 4 | Engine version is missing or invalid, including older 2.0 health responses |

When several checks fail, the order is health, major-version mismatch, readiness, then unknown version.

`RespawnedClient.status()` provides the same report for Python callers:

```python
from respawned.client import RespawnedClient

status = RespawnedClient("http://127.0.0.1:8000", timeout=5).status()
print(status["engine_version"], status["readiness"]["status"])
```

See the [agent guide](AGENT_INTEGRATION.md#connect) for connection setup before making authenticated workflow calls.

## Ingest records

Use `respawned import --file records.json`, **Setup → Records & sources**, or:

```bash
curl --fail-with-body "$RESPAWNED_API_URL/v1/workflow/import" \
  -H "Authorization: Bearer $RESPAWNED_REVIEW_TOKEN" \
  -H 'Content-Type: application/json' --data-binary @records.json
```

The body contains `opportunities` and `activities` arrays. The workflow import accepts up to 1,000 combined items within 2 MB. Success reports `opportunities_upserted` and `activities_inserted` after commit.

```json
{
  "opportunities": [{
    "id": "ats:application-123",
    "kind": "job_application",
    "title": "Backend Engineer at Example",
    "status": "open",
    "created_at": "2026-09-01T15:00:00Z",
    "contact_key": "ats:recruiter-22",
    "contact_name": "Alex",
    "contact_email": "alex@example.com",
    "preferred_channel": "email",
    "context": {
      "company": "Example",
      "role": "Backend Engineer",
      "stage": "Applied",
      "summary": "Application submitted through Alex's referral."
    }
  }],
  "activities": []
}
```

Replace example identities, dates, recipients, and facts with source data. Omit contact identity and routes when no human recipient is known. Such records remain trackable but cannot become outreach candidates.

### Field rules

| Field | Contract |
| --- | --- |
| Record `id`, `status`, `created_at` | Required; status is `open`, `won`, or `lost`; timestamps need a UTC offset |
| `contact_key` and contact routes | Both present together, or both absent; routes are `contact_email` and `contact_phone` |
| `preferred_channel` | Optional `email` or `sms`; requires a contact route |
| `contact_name`, `owner_name`, `last_contact_at` | Optional source facts; dates need a UTC offset |
| `kind`, `title` | Kind defaults to `generic` and matches `[a-z][a-z0-9_]{0,63}`; optional title allows 300 characters |
| `value` | Optional nonnegative decimal, up to ten integer and two fractional digits; values requiring rounding are rejected |
| `context` | Only `company` (200 characters), `role` (200), `stage` (100), `summary` (2,000), `expected_reply_at` (aware timestamp), `source_url` (2,048) |
| Activity `id`, `opportunity_id`, `type`, `occurred_at` | Required stable identity, referenced record, event type, and aware timestamp |
| Activity `channel`, `direction`, `classification` | Optional channel; `inbound` or `outbound`; `human`, `automated`, or `unknown` (default) |
| Activity `summary`, `source_url` | Optional, up to 2,000 and 2,048 characters respectively |

Supplied text fields must be nonempty. Source URLs use HTTP(S) without embedded credentials. Extra fields are rejected. See the [validated contract](../src/respawned/core/contracts.py).

The built-in policy recognizes `opportunity_created`, `content_viewed`, `contact_replied`, `message_sent`, `opportunity_won`, and `opportunity_lost`. Human inbound events may use a source-specific type. Unclassified legacy `contact_replied` events retain reply semantics; explicitly automated events never count as human responses.

The reply inbox requires `direction: "inbound"`. Record actual outbound events as `message_sent` with `direction: "outbound"` and the source timestamp.

### Replay and ordering

- Records upsert by ID as complete snapshots. Omitted optional fields are cleared.
- Activities are immutable. Exact replay is accepted; changed content under an existing ID returns HTTP 409.
- Import a referenced record before its activities. A missing reference returns 409.
- Conflicts roll back the batch. After an uncertain result, retry the same IDs and payloads.
- Serialize record updates. There is no source-revision check to prevent older snapshots overwriting newer ones.

Source credentials, pagination, mapping, refresh, and scheduling belong to the connector. Workspaces are views over shared engine policy, contacts, and credentials.

## Candidate and review workflow

All paths in this table use `/v1/workflow` and operator authentication.

| Method and path | Request or result |
| --- | --- |
| `POST /sync` | `{"limit":10,"dry_run":false}`; limit 1–200, default 200 |
| `GET /queue` | Latest published ranked candidates as `items`; `has_more: false` |
| `GET /records`, `GET /records/{record_id}` | Current records, contacts, evidence, reasons, and saved drafts |
| `POST /records/{record_id}/draft` | Optional `{"body":"…"}`; omitted body uses the server model |
| `POST /candidates/{candidate_id}/draft` | Same draft input, restricted to a candidate in the current queue |
| `GET /drafts/{draft_id}` | Persisted copy, recipient, status, metadata, version, and outbox ID |
| `POST /drafts/{draft_id}/edit` | `{"body":"…","review_token":"…"}` |
| `POST /drafts/{draft_id}/approve` | `{"review_token":"…"}` |
| `POST /drafts/{draft_id}/reject` | `{"review_token":"…"}` |

Sync returns `candidate_count`, `inserted_count`, `run_id`, and `dry_run`. A dry run writes nothing and returns a null `run_id`; `inserted_count` reports how many new candidates it would insert. Real sync publishes a bounded queue without drafting or fetching source updates.

Queue items include `id`, `run_at`, `primary_opportunity_id`, contact identity and route, `reason`, `score`, and `other_opportunity_ids`. Reading the queue does not refresh it.

### Drafts

Supplied text uses the same validation as engine-generated copy and requires no model configuration. Bodies allow at most 10,000 characters at the API boundary; server policy may impose a lower copy limit. Supplied text is trimmed at its edges and receives no automatic sign-off.

```bash
curl --fail-with-body \
  "$RESPAWNED_API_URL/v1/workflow/records/ats:application-123/draft" \
  -H "Authorization: Bearer $RESPAWNED_REVIEW_TOKEN" \
  -H 'Content-Type: application/json' --data-binary @draft.json
```

Here `draft.json` contains `{"body":"your draft text"}`. Submit `{}` to use the engine's configured backend.

Draft responses include `id`, `body`, `status`, `review_token`, `validation_errors`, and nullable `outbox_id`. Draft-detail and candidate-draft responses also include candidate and record IDs, saved contact details, channel, and timestamps.

Identical existing copy is reused. Different text returns 409; read the current draft and use its version token to edit. Record-level drafting evaluates current eligibility without replacing the published queue.

### Review

Edits, approvals, and rejections submit the displayed `review_token`. A stale version returns 409; reopen the current draft. Approval also rechecks the recipient, referenced records, and contact-wide cooldown before reserving one outbox item.

`respawned review` evaluates a bounded queue and always asks for human decisions. Enter skips. New copy is generated only when a draft is missing. Existing drafts can be reviewed without a model.

Explicit review records human authorization even when automatic processing is configured. Rejection suppresses unchanged candidate evidence; relevant record, route, or evidence changes can produce a new candidate.

Workspace filters do not weaken engine-wide contact grouping or cooldowns.

## Settings and workspaces

These operator routes also use the `/v1/workflow` prefix.

| Method and path | Purpose |
| --- | --- |
| `GET /setup` | Database, model, source, and outbox configuration status |
| `PUT /setup/model` | Save non-secret model settings; credentials remain in server environment variables |
| `GET /config` | Read review policy and copy limits |
| `GET /overview` | Read workspace counts without importing, evaluating, or drafting |
| `GET /workspaces` | List saved views and available record kinds |
| `POST /workspaces` | Create a view; returns 201 |
| `PUT /workspaces/{id}` | Replace its name, description, and kinds |
| `DELETE /workspaces/{id}` | Remove the view; returns 204 and preserves records |

Workspace writes contain `name` (1–120 characters), optional `description` (up to 2,000), and `kinds` (up to 100 unique type identifiers). An empty kinds list includes all record types. Responses include the view ID and timestamps. See [browser setup](WEB_UI.md) and the running engine's schemas for model configuration fields.

## Processing and automatic authorization

`respawned process --limit 10` or `POST /v1/process` explicitly processes a batch. Operator access is sufficient. `RESPAWNED_PROCESS_TOKEN` is an optional narrower credential for callers that only need processing.

```bash
curl --fail-with-body "$RESPAWNED_API_URL/v1/process" \
  -H "Authorization: Bearer $RESPAWNED_REVIEW_TOKEN" \
  -H 'Content-Type: application/json' -d '{"limit":10}'
```

Limit is an integer from 1 to 50. The server controls policy, time, and authorization actor.

| Server policy | Result |
| --- | --- |
| `review.mode: human` (default) | Pending drafts awaiting review |
| `review.mode: automatic` | Valid eligible drafts become approved outbox reservations |

Processing never sends messages. Changing policy affects later processing, not existing reservations or an already running batch. CLI `review` continues to prompt under either policy.

Inspect every item outcome: `pending`, `authorized`, `blocked`, or `already_reviewed`. HTTP 200 does not mean every item succeeded. Completed items commit independently; a later failure can leave earlier progress. Retrying reuses persisted work.

## Inbox and outbox

`respawned inbox --json` and `GET /v1/workflow/inbox` show unanswered human replies, including during outreach cooldown. Pending outbox messages are not sent answers. The response includes reply evidence, routes, related records, and pending-outbox counts.

`GET /v1/workflow/outbox` reads reservation snapshots. `GET /v1/workflow/outbox/export?format=json` exports exact strings; `format=csv` produces spreadsheet-safe CSV. An optional `workspace_id` filters whole reservations. Export does not change status.

```bash
respawned inbox --json
respawned outbox --pending --json
respawned outbox --path outbox.csv
```

CSV protects formula-like cells, so use JSON for sending integrations. Pagination is a snapshot read, not a worker claim or synchronization cursor.

## Outbox integration

A sending service fetches approved messages, delivers them through its provider, and reports the confirmed result. These routes accept `RESPAWNED_OUTBOX_TOKEN` or operator credentials. The outbox token does not grant import, drafting, processing, or approval access.

| Method and path | Result |
| --- | --- |
| `GET /v1/outbox/pending?limit=50` | Oldest approved pending messages as `items` and `has_more`; limit 1–200 |
| `GET /v1/outbox/{id}` | Approved snapshot and nullable `receipt` |
| `POST /v1/outbox/{id}/receipt` | Record a confirmed send and return the updated snapshot |

### Fetch approved messages

```bash
curl --fail-with-body "$RESPAWNED_API_URL/v1/outbox/pending?limit=50" \
  -H "Authorization: Bearer $RESPAWNED_OUTBOX_TOKEN"
```

Each item contains `id`, `draft_id`, `contact_key`, `contact_address`, `contact_name`, `channel`, `opportunity_ids`, `body`, `status`, `authorization_mode`, `created_at`, and `sent_at`. Use the returned recipient, channel, and exact body. Authorization is `human` or `automatic`; old `legacy_unknown` reservations are excluded.

Fetching does not claim work. After recording results, poll again from the beginning. Do not save the highest numeric ID as a checkpoint: an earlier ID can commit later.

### Record a confirmed send

After the provider confirms a send, save its result as `receipt.json`:

```json
{
  "sender": "mail:account-123",
  "provider_message_id": "provider-message-456",
  "sent_at": "2026-09-10T10:30:00Z"
}
```

Replace the example values and outbox ID with the actual result:

```bash
curl --fail-with-body "$RESPAWNED_API_URL/v1/outbox/42/receipt" \
  -H "Authorization: Bearer $RESPAWNED_OUTBOX_TOKEN" \
  -H 'Content-Type: application/json' --data-binary @receipt.json
```

| Field | Contract |
| --- | --- |
| `sender` | Provider/account namespace, 1–200 characters; starts with a letter or digit, then letters, digits, `_ . : @ / -` |
| `provider_message_id` | Nonempty provider result ID, up to 300 characters; its pair with `sender` identifies one message |
| `sent_at` | Timestamp with a UTC offset, at or after approval and no later than the server clock |

Success returns the approved snapshot with `status: "sent"`, `sent_at`, and `receipt: {sender, provider_message_id, sent_at, recorded_at}`. The receipt, outbox status, and outbound activities for related records commit together. Approved copy and recipient remain unchanged.

An identical retry returns the saved result. Different facts for the same item, or reuse of one sender/message pair for another item, return 409.

### Python clients

The installed SDK provides `RespawnedClient.pending_outbox()` and `record_receipt(id, receipt)`, using the same connection as the CLI.

For an independent sender, [download outbox_client.py](https://respawned.williamshayden.com/examples/outbox_client.py). It needs Python 3.12+ with no third-party dependencies and works with Respawned 1.1.0 or later:

```bash
curl -fsS https://respawned.williamshayden.com/examples/outbox_client.py \
  -o outbox_client.py
python3 outbox_client.py pending --limit 50
python3 outbox_client.py get 42
python3 outbox_client.py receipt 42 receipt.json
```

Set `RESPAWNED_API_URL` (default `http://127.0.0.1:8000`) and `RESPAWNED_OUTBOX_TOKEN` in its environment. Successful responses are JSON on stdout; errors use stderr and a nonzero exit code.

The file exports `OutboxClient.pending(limit=50)`, `get(id)`, and `receipt(id, receipt_dict)`. Requests use a 30-second network timeout and responses are limited to 4 MiB. It refuses redirects, ignores environment proxies, and performs no implicit retries or sending.

### Retries and delivery ownership

Coordinate one logical sender per engine with durable state. Use a provider idempotency key based on the stable draft UUID and engine identity. Save the approved snapshot before sending and persist confirmed receipts before acknowledging them.

If a send times out, reconcile with the provider before trying again. If receipt submission times out, read the outbox item or retry the identical saved receipt. Receipt idempotency prevents duplicate records; it does not prevent duplicate provider sends.

The sender checks source freshness and whether the message should still be sent. A receipt records something that already happened, so it does not rerun eligibility or change the approved route. Activities stay attached to the original record IDs; changing a record's contact changes how its history is projected.

Respawned records the sender's report without contacting the provider. This interface has no claim leases or failed/cancelled transitions.

## Errors

| Status | Meaning |
| --- | --- |
| 401 | Missing or invalid credential for an enabled operation |
| 404 | Missing resource or unconfigured access |
| 409 | Conflicting source facts, stale review, ineligible action, or conflicting receipt |
| 422 | Invalid input or draft validation failure |
| 503 | Database, policy, or requested drafting backend unavailable |

After an uncertain write, read persisted state before claiming success or repeating the action. New receipt writes require a pending item with human or automatic authorization.

## Policy and drafting context

Server policy controls timezone, cooldown, expiry, sender/sign-off, copy limits, reasons, and ranking. Configure `RESPAWNED_POLICY_PATH` on the engine; client commands do not supply policy or time overrides.

Ranking combines the strongest eligible reason with value and signal weights. Job applications are excluded from value ranking. Rules cover unanswered human replies, viewing activity, aging work, applications without a human update, and missed expected replies. A newer human reply or outbound suppresses an older promised-update deadline.

Drafting receives bounded record, contact, tone, and sender context. The engine validates generated and supplied text for length, unresolved placeholders, prohibited currency amounts, and referenced-record eligibility. See [backend setup](WEB_UI.md#connect-a-model-backend), [policy loading](../src/respawned/core/policy.py), and [evaluators](../src/respawned/core/reasons.py).

## Upgrading to 2.0

- The 2.0 release moved workflow commands to the API; remove database credentials from client-only environments. `init`, `serve`, and `ui` remain engine lifecycle commands.
- Local `ui`, or loopback `serve` without an operator token, creates private CLI access. Remote clients use `RESPAWNED_API_URL` and an operator credential.
- `--now` and `--policy` are no longer workflow-client options. Policy belongs on the server; fixed time belongs in simulation setup.
- Data routes `/v1/ingest`, `/v1/inbox`, `/v1/drafts`, and `/v1/outbox` now require operator authentication. Their response shapes are preserved.
- New integrations use `/v1/workflow`. Existing `/v1/ui` routes remain compatibility aliases. The separate `/v1/outbox` connector routes are unchanged.
- `review` always asks for human decisions. Use explicit `process` for server-policy processing.

The 2.0.0 CLI and Python SDK were qualified with the 2.0.0 engine. The browser UI ships with its engine release. For current version checks and compatibility, see [Engine status and versions](#engine-status-and-versions).

Stop the engine and back up PostgreSQL before upgrading. Install the current package, run `respawned init` on the engine host, restart the engine, and reconnect clients. See the [installation guide](../README.md#install-and-start) for the current installer.
