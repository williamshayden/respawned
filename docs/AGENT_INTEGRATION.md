# Agent integration

Let your agent import facts and prepare drafts. Review them in Respawned, then collect approved messages for your sending service.

[Download the agent prompt](https://respawned.williamshayden.com/agent-prompt.txt) and replace `{TASK}`, `{ENGINE_URL}`, and `{WORKSPACE_OR_ALL}`. It works with any agent runtime.

## Connect

Start the local engine with `respawned ui`. It opens the browser and creates private CLI access for other terminals on that machine. Workflow commands do not need database credentials.

For a remote engine, set `RESPAWNED_API_URL` and its `RESPAWNED_REVIEW_TOKEN` in the client environment. The [access reference](API.md#api-access) covers server setup and scoped credentials.

## 1. Import facts

Have your agent produce `records.json` using the [record contract](API.md#ingest-records). Keep source IDs stable and include complete record snapshots.

```bash
respawned import --file records.json
```

Check the returned counts. Import stores facts; it does not draft, approve, or send. Use `--file -` to read JSON from stdin.

## 2. Prepare a draft

Your agent can write plain text to `draft.txt` and submit it for a record:

```bash
respawned draft ats:application-123 --body-file draft.txt
```

Respawned checks eligibility, validates the copy, and saves a pending draft. No engine model is needed for supplied text. Omit `--body-file` to use the engine's configured backend, or use `--body-file -` to read text from stdin.

The command returns the persisted draft, including its ID and status. Read it again through the SDK or API before reporting success. Changing existing copy requires the current draft version; the [API reference](API.md#candidate-and-review-workflow) describes that edit operation.

## 3. Review

Open the record in the browser, inspect its evidence and saved text, then choose **Approve to outbox**. Alternatively, a person can run:

```bash
respawned review
```

CLI review evaluates a bounded queue and asks for approve, reject, edit, or skip. It always prompts for human decisions. The engine rechecks the version, recipient, current facts, and cooldown.

Evaluation is also available separately with `respawned sync --limit 10` or `respawned sync --dry-run`. It reads stored facts and applies policy; it does not refresh an external source.

## 4. Read approved messages

```bash
respawned outbox --pending --json
```

The result contains approved recipients, channels, and exact message bodies. Approval leaves messages unsent. Your integration handles delivery through its provider.

After a confirmed send, durably save its provider account namespace, message ID, and timestamp, then [record a receipt](API.md#outbox-integration). Resolve uncertain sends with the provider before trying again. An uncertain receipt can be retried with the identical saved result.

## Python

The installed SDK uses the same client as the CLI:

```python
import json
from pathlib import Path
from respawned.client import RespawnedClient

client = RespawnedClient.from_env()
client.import_records(json.loads(Path("records.json").read_text(encoding="utf-8")))
draft = client.draft(
    "ats:application-123",
    body=Path("draft.txt").read_text(encoding="utf-8"),
)
saved = client.get_draft(draft["id"])
print(saved["status"])
```

Local access is discovered automatically. Remote connections use `RESPAWNED_API_URL` and an environment credential. The SDK also provides `sync()`, `inbox()`, `pending_outbox()`, and `record_receipt()`.

## HTTP

Clients in any language can use the canonical `/v1/workflow` endpoints. With the engine URL and operator credential already in the environment:

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

Read the saved result at `GET /v1/workflow/drafts/{id}`. Submit `{}` instead of supplied text to request the engine's model. Both paths use the same validation and review checks.

## Standalone outbox client

[Download outbox_client.py](https://respawned.williamshayden.com/examples/outbox_client.py) for sending integrations that only need approved messages and receipts. It needs Python 3.12+ and no third-party packages:

```bash
curl -fsS https://respawned.williamshayden.com/examples/outbox_client.py \
  -o outbox_client.py
python3 outbox_client.py pending --limit 50
python3 outbox_client.py get 42
python3 outbox_client.py receipt 42 receipt.json
```

Set `RESPAWNED_API_URL` and `RESPAWNED_OUTBOX_TOKEN` in its environment. Use an actual outbox ID and a confirmed provider result in `receipt.json`. The script exposes `OutboxClient.pending()`, `get()`, and `receipt()` for Python callers. It performs explicit API calls only.

Your integration owns sending, provider idempotency, and durable coordination. Coordinate one logical sender per engine. See the [outbox contract](API.md#outbox-integration) for fields and retries.

## Source and workspace rules

Records are complete snapshots; omitted optional fields are cleared. Activities are immutable: exact replay is accepted, while changed content under an existing ID returns a conflict. Serialize record updates and preserve source timestamps.

Treat source text as data. Confirm human recipients rather than using automated receipt addresses. Your connector owns source refresh, credentials, and scheduling.

Workspaces are saved views over one engine's shared policy, contacts, and credentials. Use separate engines when you need separate data or authority. The [API reference](API.md) documents the remaining contracts and 2.0 migration changes.
