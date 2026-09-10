# Documentation

Track follow-ups, review drafts, and record confirmed sends from your own tools. Respawned supports job applications, sales, and other record types through one workflow.

The browser, CLI, and Python SDK call the same API. The engine owns policy, validation, review, and stored state. Your agent can supply draft text or request a configured model backend.

[Agent integration](/agent-integration/) · [Agent prompt](/agent-prompt.txt) · [API reference](/api/)

## Install and start locally

The installer needs `curl`, a POSIX shell, and Python 3.12+ with `venv` and `ensurepip`. It supports Linux, macOS, and WSL; release qualification runs on Linux. PostgreSQL is a separate server requirement.

[View installer](/install.sh) · [Download package](/downloads/respawned-2.0.0-py3-none-any.whl) · [Checksums](/SHA256SUMS)

```bash
curl -fsS https://respawned.williamshayden.com/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

The installer verifies the package checksum, installs it in `~/.local/share/respawned/2.0.0`, and adds `~/.local/bin/respawned`. The package includes the CLI, API, SDK, and built browser UI. No repository clone or Node.js is needed.

To inspect the installer first:

```bash
curl -fsS https://respawned.williamshayden.com/install.sh -o install.sh
# Read install.sh, then run it.
sh install.sh
```

Use `sh install.sh --prefix /absolute/path` for another prefix. A [source archive](/downloads/respawned-2.0.0.tar.gz), [package provenance](/release.json), and [site manifest](/site-manifest.json) are also available.

### Configure PostgreSQL

On the engine host, create a PostgreSQL database and role, then set its connection values:

```bash
export DB_HOST=127.0.0.1
export DB_PORT=5432
export DB_NAME=respawned
export DB_USER=respawned
export DB_PASSWORD='your-database-password'
respawned init
respawned ui
```

The app opens at `http://127.0.0.1:8000` with browser access connected. The launcher creates private CLI access for other terminals on this machine. Database settings belong to the engine; workflow clients do not need them.

Engine commands do not automatically read `.env`. Keep database settings in the host shell or service configuration. Startup creates no sample records and needs no model credentials.

Use `respawned ui --port 8001` for another port or `--no-open` to print the browser link. Set `RESPAWNED_API_URL` to that loopback URL in CLI terminals using a custom port. Leave the engine running while using clients. Stop it with Ctrl+C; PostgreSQL remains separate.

## CLI and agents

Have your agent produce canonical `records.json` and plain draft text in `draft.txt`. With the local engine running, use another terminal:

```bash
respawned import --file records.json
respawned draft ats:application-123 --body-file draft.txt
respawned review
respawned outbox --pending --json
```

Import saves facts. Draft submission checks eligibility and validates text. A person reviews the pending draft before it appears in the approved outbox.

Omit `--body-file` to request the engine's model. `review` evaluates a bounded queue and always asks for human decisions. `sync --dry-run` previews eligibility; `process --limit 10` explicitly processes a batch under server policy.

For a remote engine, set `RESPAWNED_API_URL` and `RESPAWNED_REVIEW_TOKEN`. The [agent guide](/agent-integration/) covers CLI, SDK, and HTTP use. [Download its prompt](/agent-prompt.txt) to give an agent the workflow and boundaries.

## Import source records

Open **Setup → Records & sources**. Choose a JSON file or paste a payload, check the item counts, and select **Import records**. Use **Review imported records** to continue to the queue.

The [record contract](/api/#ingest-records) defines `opportunities` and `activities`. Import accepts up to 1,000 combined items within 2 MB. It stores facts without generating drafts or sending messages.

Use stable source IDs. Records are complete snapshots, so omitted optional fields are cleared. Activities are immutable; identical replay is accepted, while conflicting content rolls back the batch.

A record without a confirmed human recipient remains visible in **All tracked** but cannot become an outreach candidate. Keep automated receipt addresses separate from human contacts.

## Create and monitor workspaces

Choose **Workspaces → New workspace**, enter a name and record types, then save. **Include all** covers every current and future type. **Add a record type** prepares a view before importing it.

Use matching lowercase `kind` identifiers such as `job_application`, `partnership`, or `project`. Workspaces share engine policy, contacts, model settings, credentials, and outbox. Removing a workspace removes its view, not its data.

**Overview → Choose workspaces** selects several views to monitor, including views on other engines. Checks run every 30 seconds while visible and can be paused. Failures retain marked stale counts. Counts can overlap between views.

## Configure a drafting backend

An engine model is needed only when you ask it to write new copy. Agents can submit their own text, and saved drafts remain reviewable without a model.

Under **Setup → Model backend**, choose an OpenAI-compatible API or the optional Codex CLI adapter. Saved settings apply to requests from the browser, CLI, and API.

For an API backend, enter its URL, model name, timeout, and credential environment variable. Set the secret in `RESPAWNED_MODEL_API_KEY` or `LITELLM_MASTER_KEY` on the server and restart. The browser stores the variable name, not the secret. LiteLLM is optional.

For Codex, install and authenticate the runtime on the engine host, select `codex_cli`, and generate a draft to invoke it. Saving settings does not run login or availability probes. The engine validates accepted text and keeps all review checks.

Set the server policy's sender/sign-off before generating copy. Configuration saves do not call the backend.

## Review and outbox

Open a record in **Review queue** and inspect its reason, facts, and history. Generate a draft or review supplied text, edit and save as needed, then choose **Approve to outbox**.

**Evaluate queue** applies policy to stored facts. **Refresh** reloads the view. Neither fetches source updates; evaluation does not draft or send.

The server rechecks the displayed version, recipient, current records, and contact-wide cooldown before approval. Reopen the draft if another client changed it. The default policy requires human review.

For spreadsheet export, download CSV in **Outbox** or run `respawned outbox --path outbox.csv`. JSON preserves exact text; CSV protects formula-like cells. Export does not change status.

A sending service can fetch approved messages from `GET /v1/outbox/pending` and submit confirmed results to `POST /v1/outbox/{id}/receipt`. Outbox status and related outbound history update together.

[Outbox API reference](/api/#outbox-integration) · [Download Python client](/examples/outbox_client.py)

The standalone client needs Python 3.12+ and no third-party packages. It reads `RESPAWNED_API_URL` and `RESPAWNED_OUTBOX_TOKEN` from the environment. Your integration owns sending, provider idempotency, and durable coordination.

## Access and draft version tokens

| Mechanism | Purpose |
| --- | --- |
| Local browser session and private CLI access | Created by the local launcher; no token copying |
| `RESPAWNED_REVIEW_TOKEN` | Operator access for remote UI, CLI, SDK, and HTTP clients |
| `RESPAWNED_PROCESS_TOKEN` | Optional processing-only credential for an agent |
| `RESPAWNED_OUTBOX_TOKEN` | Approved-message reads and confirmed-send receipts |
| Draft `review_token` | Version fingerprint for edits and review; not a password |

The one-use local browser link lasts five minutes. Its session lasts up to 12 hours, until **Lock access**, or until server shutdown. Private CLI access is separate and ends when its server stops.

For remote access, configure `RESPAWNED_REVIEW_TOKEN` on the engine and restart. Enter it under **Setup → Engine access** or provide it in the client environment. Manual browser credentials clear on reload.

Operator credentials can process batches. A separate processing token is needed only when the caller should have that narrower permission. Outbox credentials do not grant drafting, processing, or approval.

## Connect another engine

Run the other engine with its own database and operator token, behind HTTPS or a loopback tunnel. Configure `RESPAWNED_UI_ORIGINS` on that engine to allow the browser application's exact origin:

```dotenv
RESPAWNED_UI_ORIGINS=http://127.0.0.1:8000
```

**Connections → Prepare a remote engine** shows the correct origin. Comma-separate multiple origins; omit paths, trailing slashes, and wildcards.

In **Connections**, enter the engine name, API root URL, and access token, then choose **Connect engine**. Select its views in Overview or use **Open engine**. Manual tokens stay in tab memory; names, URLs, and watch choices persist.

Remote URLs require HTTPS; loopback HTTP is allowed. Browser requests carry only the selected engine's credential. The origin allowlist permits the browser connection; it does not replace authentication.

## API reference and troubleshooting

The [API reference](/api/) covers routes, payloads, errors, and retries. The running engine serves schemas at `/docs` and `/openapi.json`. Use `respawned serve --api-only` for an engine without the bundled interface.

`/healthz` checks process liveness. `/readyz` checks database, schema, and policy; it does not test a model.

| Symptom | Check |
| --- | --- |
| Database unavailable | Engine-host `DB_*` values and PostgreSQL readiness |
| CLI cannot connect | Engine process, API URL, and local or remote access |
| Nothing ready for review | **All tracked**, confirmed recipient, status, policy, and cooldown |
| Draft generation fails | Backend, model access, server credential, and timeout |
| Write timed out | Read saved state before repeating the action |
| Remote UI fails | HTTPS URL, engine credential, and exact allowed origin |

## Upgrading to 2.0

Back up the database, upgrade the engine, run `respawned init`, and restart it. Reconnect clients afterward.

Workflow CLI commands now use the API. Remove database settings from client-only environments, and configure remote clients with the engine URL and credential. `--now` and `--policy` are server/simulation concerns, not workflow-client options.

All data routes now require authentication. New clients use `/v1/workflow`; existing `/v1/ui` aliases remain available. See the [migration reference](/api/#upgrading-to-20).
