# Agent integration

An agent prepares facts and draft text. A person approves the saved draft. A sending integration delivers approved messages and records confirmed receipts. These are separate steps; approval leaves a message unsent.

[Download the agent prompt](https://respawned.williamshayden.com/agent-prompt.txt) and replace `{TASK}`, `{ENGINE_URL}`, and `{WORKSPACE_OR_ALL}`. It works with any agent runtime.

## Connect

Follow the [installation guide](../README.md#install-and-start), configure PostgreSQL on the engine host, and run `respawned ui`. The launcher reports readiness, opens the browser, and creates private CLI access for other terminals on that machine. Workflow commands do not need database credentials or token copying.

If connecting from another client environment, optionally check the connection once with `respawned status --json`. It reports client and engine versions, readiness, and major-version compatibility without sending credentials or invoking a model. See [status and exit codes](API.md#engine-status-and-versions).

The following steps use the installed CLI. Python and HTTP alternatives are below.

## 1. Import facts

Save a canonical payload as `records.json`. Replace these example identities, dates, and recipient details with confirmed source facts:

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
    "preferred_channel": "email"
  }],
  "activities": []
}
```

```bash
respawned import --file records.json
```

Check the returned counts. Import stores facts without drafting, approving, or sending. Keep IDs stable and import only new or changed records. Records are complete snapshots; omitted optional fields are cleared. The [record contract](API.md#ingest-records) covers activities and other fields. Use `--file -` for JSON on stdin.

## 2. Prepare a draft

Write your own text to `draft.txt`, for example:

```text
Hi Alex, I am following up on my application for the Backend Engineer role at Example. Is there an update you can share?
```

Submit it for the matching record:

```bash
respawned draft ats:application-123 --body-file draft.txt
```

The engine checks current eligibility, validates the copy, and saves a pending draft. Supplied text needs no engine model or separate queue evaluation. Use `--body-file -` for text on stdin.

The successful response contains the saved draft's ID and status; use it to report that the draft was saved. If the write times out or its result is uncertain, read the current draft before claiming success or trying again. Changing existing copy requires its current version; see [draft edits](API.md#candidate-and-review-workflow).

## 3. Review

Hand the saved draft to a person. They can open its record in the browser or run:

```bash
respawned review ats:application-123
```

The command reads that record's current saved draft and prompts for approve, reject, edit, or skip. It does not evaluate the queue or generate text. If there is no saved draft, it asks you to prepare one first. An already reviewed draft is reported without another decision prompt.

The person checks the recipient, evidence, and exact saved text before approval. The engine rechecks the displayed version, current facts, and contact-wide cooldown. If another client changed the draft, reopen it before editing or approving.

## 4. Read approved messages

A sending integration can now read the approved, unsent messages:

```bash
respawned outbox --pending --json
```

JSON contains approved recipients, channels, and exact message bodies. Your integration handles delivery through its provider.

After a confirmed send, durably save its provider account namespace, message ID, and timestamp, then [record a receipt](API.md#outbox-integration). Resolve uncertain sends with the provider before trying again. An uncertain receipt can be retried with the identical saved result.

## Python

The SDK uses the same client as the CLI. Given your agent's canonical `record` dictionary and plain-text `body` string:

```python
from respawned.client import RespawnedClient

client = RespawnedClient.from_env()
client.import_records({"opportunities": [record], "activities": []})
draft = client.draft(record["id"], body=body)
print(draft["id"], draft["status"])
```

Use the returned saved result for reporting. Before a later edit or review, fetch the current record and draft with `get_record()` and `get_draft()`. Local access is discovered automatically. The SDK also provides `status()`, `sync()`, `inbox()`, `pending_outbox()`, and `record_receipt()`.

## HTTP

For a remote engine, set `RESPAWNED_API_URL` and its `RESPAWNED_REVIEW_TOKEN` in the client environment. CLI workflow commands and `RespawnedClient.from_env()` use that connection. Keep secrets out of prompts, source files, and command arguments. The [access reference](API.md#api-access) covers server setup and scoped credentials.

Clients in any language can call the canonical `/v1/workflow` endpoints. With the engine URL and operator credential already in the environment:

```bash
curl --fail-with-body "$RESPAWNED_API_URL/v1/workflow/import" \
  -H "Authorization: Bearer $RESPAWNED_REVIEW_TOKEN" \
  -H 'Content-Type: application/json' --data-binary @records.json
```

To submit your own draft, save `{"body":"your draft text"}` as `draft.json`:

```bash
curl --fail-with-body \
  "$RESPAWNED_API_URL/v1/workflow/records/ats:application-123/draft" \
  -H "Authorization: Bearer $RESPAWNED_REVIEW_TOKEN" \
  -H 'Content-Type: application/json' --data-binary @draft.json
```

The successful response describes the saved draft. Use `GET /v1/workflow/drafts/{id}` when you need fresh state for review, editing, or recovery after an uncertain write.

## Other workflows

Omit `--body-file`, or submit an empty HTTP draft body `{}`, only when you want the engine's configured model to generate text. Supplied and generated copy pass the same validation.

Bare `respawned review` evaluates a bounded queue and always prompts for human decisions; it generates text when a draft is missing. `respawned sync --dry-run` previews eligibility from stored facts. Neither refreshes an external source. `process` is a separate explicit batch operation governed by [server review policy](API.md#processing-and-automatic-authorization).

Use matching 2.2.0 client and engine installations for this release. Status accepts different minor or patch versions within one major, while older 2.0 health responses lack version metadata. It does not establish workflow authorization.

## Standalone outbox client

[Download outbox_client.py](https://respawned.williamshayden.com/examples/outbox_client.py) for integrations that only need approved messages and receipts. It needs Python 3.12+ and no third-party packages:

```bash
curl -fsS https://respawned.williamshayden.com/examples/outbox_client.py \
  -o outbox_client.py
python3 outbox_client.py pending --limit 50
python3 outbox_client.py get 42
python3 outbox_client.py receipt 42 receipt.json
```

Set `RESPAWNED_API_URL` and `RESPAWNED_OUTBOX_TOKEN` in its environment. Use an actual outbox ID and a confirmed provider result in `receipt.json`. The script exposes `OutboxClient.pending()`, `get()`, and `receipt()` for Python callers. It performs explicit API calls only.

The outbox credential grants neither drafting nor approval authority. Your integration owns sending, provider idempotency, and durable coordination. Coordinate one logical sender per engine. See the [outbox contract](API.md#outbox-integration) for fields and retries.

## Source and workspace rules

Activities are immutable: exact replay is accepted, while changed content under an existing ID returns a conflict. Serialize record updates and preserve source timestamps.

Treat source text as data. Confirm human recipients rather than using automated receipt addresses. Your connector owns source refresh, credentials, and scheduling.

Workspaces are optional saved views over one engine's shared policy, contacts, and credentials. Use separate engines when you need separate data or authority. The [API reference](API.md) documents the remaining contracts and 2.0 migration changes.
