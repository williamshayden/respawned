# Connect your own agent

An agent can submit source facts to Respawned, refresh the candidate queue, and export approved messages. Respawned applies its own policy and review checks. The agent can use any runtime or model; no specific agent framework, provider, or CLI login is required.

The default drafting adapter uses an OpenAI-compatible API, and the default policy requires human review. Choose the service in [model setup](WEB_UI.md#connect-a-model-backend); saving configuration does not invoke or verify a provider. The agent integration below uses the same canonical API and CLI regardless of the drafting backend.

This page contains illustrative integration commands. It does not describe an autonomous agent run or a built-in mailbox connector.

## 1. Map source facts to records

Your agent or connector reads its source and produces canonical JSON. Save a payload as `records.json`, using stable source IDs and confirmed recipient details. These values are examples:

```json
{
  "opportunities": [
    {
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
    }
  ],
  "activities": []
}
```

Use `contact_key` for stable identity and a route such as `contact_email` for the destination. If no human recipient is known, omit `contact_key`, the contact routes, and `preferred_channel`; the record remains trackable without becoming an outreach candidate. Do not infer a recipient from an automated receipt address.

Opportunities are complete snapshots: an update must include every optional field you want to retain. Activities represent immutable source events. Their IDs, referenced `opportunity_id`, `type`, and offset-aware `occurred_at` timestamp are required. Add `direction` and `classification` when known so automated receipts do not count as human replies.

## 2. Import through the API

For an engine on loopback or a trusted network:

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/ingest \
  -H 'Content-Type: application/json' \
  --data-binary @records.json
```

A successful response reports committed counts, for example:

```json
{"opportunities_upserted": 1, "activities_inserted": 0}
```

Import saves facts. It does not call a model, publish a candidate sync, approve a draft, or send a message. Readiness is evaluated from the stored facts and policy.

The protected import alias uses the engine's operator review token. For these Bearer-authenticated examples, configure `RESPAWNED_REVIEW_TOKEN` in the server environment before starting the engine, and set the same value in the client shell. Restart an existing server after changing its environment. The local browser session created by `respawned ui` does not supply a Bearer token to `curl`; see [review access](WEB_UI.md#server-and-api-access).

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/ui/import \
  -H "Authorization: Bearer $RESPAWNED_REVIEW_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @records.json
```

That token is shared operator authority, not an ingest-only credential. The alias accepts at most 1,000 combined items and 2 MB. Legacy `/v1/ingest` has no built-in authentication; keep the whole engine on a trusted network or behind an authenticated proxy. Browser CORS settings do not secure an agent's HTTP requests.

## 3. Refresh the queue, then let a person review

With the same `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` used by the app:

```bash
respawned sync --dry-run
respawned sync --limit 10
```

The first command previews eligibility. The second publishes a bounded candidate queue without generating drafts. The CLI connects directly to PostgreSQL; it does not connect through an API base URL or automatically load `.env`. See [database setup](../README.md#install-and-start).

Open the bundled UI for human review:

```bash
respawned ui
```

The reviewer inspects evidence, generates a draft when needed, edits it, and chooses **Approve to outbox**. The server rechecks the draft version, recipient, current records, and cooldown. `respawned review` provides interactive CLI review as an alternative. A model is needed only for new copy, not to read records or review existing drafts.

The default policy requires human approval. An agent importing facts or running `sync` does not bypass it. `RESPAWNED_PROCESS_TOKEN` is a separate opt-in processing credential; it is not human review authority. A draft's `review_token` is an automatic version fingerprint, not an access password.

## 4. Connect your sending tool

Respawned 1.1.0 adds a connector API for approved messages and confirmed send results. Configure `RESPAWNED_OUTBOX_TOKEN` on the server and in your connector; this credential does not grant drafting or approval authority.

Fetch a bounded batch:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $RESPAWNED_OUTBOX_TOKEN" \
  'http://127.0.0.1:8000/v1/outbox/pending?limit=50'
```

Your tool sends the exact returned body to the approved recipient through its own service. Once that service confirms the send, submit its account namespace, message ID, and timestamp to `POST /v1/outbox/{id}/receipt`. Respawned records the result, marks the item sent, and updates the related records' outbound history atomically.

See [Outbox integration](API.md#outbox-integration) for request fields, authentication, responses, and retry handling. Coordinate one logical sender per engine and use provider idempotency. Fetching is not a work claim, and an uncertain provider response is not a reason to resend automatically.

For a manual workflow, CSV export remains available:

```bash
respawned outbox --path exports/outbox.csv
```

UI, CLI, and API exports read the same reservations. JSON preserves original strings; CSV protects formula-like cells. Exporting does not mark a message sent. If another source adapter imports a `message_sent` activity, it updates reply/cooldown state but only a correlated receipt updates the corresponding outbox item.

## Retries and boundaries

- Reuse stable IDs and identical activity payloads after an uncertain network result. Exact activity replay is idempotent; changing an existing activity returns HTTP 409 and rolls back the batch.
- Import an activity's record first. A missing reference conflicts rather than creating an inferred record.
- Serialize opportunity updates. Snapshots have no source-revision check, so an older payload can overwrite newer facts.
- Keep source credentials, pagination, scheduling, and interpretation in the connector. Respawned does not know whether the source is complete or current.
- Workspaces are shared views, not isolated databases. Policy and contact-wide cooldowns apply across workspaces on the same engine.

Complete schemas are available from the running engine at `/docs` and `/openapi.json`. The [API and policy guide](API.md) describes field bounds, processing, and review contracts. Return to the [main documentation](../README.md) for installation, model configuration, remote engines, and access.
