# Documentation

Track follow-ups, review drafts, and record confirmed sends from your own tools. Respawned supports job applications, sales, and other record types through one workflow.

An agent or person prepares a draft. A person approves it to the unsent outbox. A sending integration delivers the approved text and records the confirmed receipt. The browser, CLI, and Python SDK share one API; the engine owns policy, validation, and stored state.

[Agent integration](/agent-integration/) · [Agent prompt](/agent-prompt.txt) · [API reference](/api/)

## Install and start locally

You need `curl`, a POSIX shell, Python 3.12+ with `venv` and `ensurepip`, and a PostgreSQL database. The installer supports Linux, macOS, and WSL; release qualification runs on Linux.

```bash
curl -fsS https://respawned.williamshayden.com/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

The package includes the CLI, API, SDK, and prebuilt browser UI. [View the installer](/install.sh) or see [installation options](#installation-options).

### Configure PostgreSQL

On the engine host, create a PostgreSQL database and role, then set their actual connection values:

```bash
export DB_HOST=127.0.0.1
export DB_PORT=5432
export DB_NAME=respawned
export DB_USER=respawned
export DB_PASSWORD='your-database-password'
respawned ui
```

The engine initializes its schema and checks database and policy readiness. The launcher reports readiness and opens `http://127.0.0.1:8000` with browser access connected. Leave it running while using the app. Startup creates no sample records and needs no model credentials.

## Import source records

Start with one real record. Open **Setup → Records & sources**, paste a payload or choose a JSON file, and select **Import records**. Replace the example identity, date, and recipient below with confirmed source facts. No workspace or model setup is required.

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

Use **Review imported records** to open the queue, then open your record. Import saves facts; eligibility still depends on the server's policy. **All tracked** shows waiting records and their reasons. A record without a confirmed human recipient remains trackable but cannot become an outreach candidate.

Save the same payload as `records.json` for CLI use. The [record contract](/api/#ingest-records) covers activities and additional fields. Import accepts up to 1,000 combined items within 2 MB. Records are complete snapshots, so omitted optional fields are cleared. Activities are immutable: exact replay is accepted and conflicting content rolls back the batch.

## Review and outbox

Inspect the record's reason, facts, and history. Choose **Write draft**, enter your own text, and select **Save draft**. Replace this example with your actual message; CLI users can save it as `draft.txt`:

```text
Hi Alex, I am following up on my application for the Backend Engineer role at Example. Is there an update you can share?
```

A person then checks the saved text and recipient and chooses **Approve to outbox**. Saving and approval are separate actions. The server rechecks the displayed version, current records, recipient, and contact-wide cooldown before approval. Reopen a draft changed by another client.

The approved message appears in **Outbox**, still unsent. **Refresh** reads current engine state; it does not fetch source updates, draft, or send.

For a spreadsheet, download CSV in **Outbox** or run `respawned outbox --path outbox.csv`. JSON preserves exact text; CSV protects formula-like cells. Reading or exporting does not change status.

## CLI and agents

The local launcher creates private CLI access for other terminals on that machine. Workflow clients do not need database settings or token copying. Optionally run `respawned status` from the client terminal to check engine readiness.

An agent prepares the record and draft:

```bash
respawned import --file records.json
respawned draft ats:application-123 --body-file draft.txt
```

The successful response identifies the saved draft and its status. No extra read or queue evaluation is needed to report that it was saved. Supplied text needs no engine model.

A person reviews that saved draft:

```bash
respawned review ats:application-123
```

Review opens the current draft and prompts for a human decision. This selected-record mode does not generate copy or evaluate the queue.

The [agent guide](/agent-integration/) covers SDK and HTTP alternatives, remote connections, and uncertain-write recovery. [Download the prompt](/agent-prompt.txt) to give an agent the workflow and boundaries.

## Connect a sending integration

Your sender reads approved messages from `GET /v1/outbox/pending`, delivers the exact approved text through its provider, then posts a confirmed result to `POST /v1/outbox/{id}/receipt`. The outbox status and related outbound history update together.

Use the dedicated `RESPAWNED_OUTBOX_TOKEN`, which grants neither drafting nor approval authority. Coordinate one logical sender per engine and use durable state and provider idempotency. Reconcile uncertain sends with the provider before trying again.

[Outbox API reference](/api/#outbox-integration) · [Download Python client](/examples/outbox_client.py)

The standalone client needs Python 3.12+ and no third-party packages. It reads `RESPAWNED_API_URL` and `RESPAWNED_OUTBOX_TOKEN` from the environment.

## Other CLI workflows

`respawned inbox --json` reads unanswered human replies. `outbox --pending --json` reads approved exact text. Bare `review` evaluates a bounded queue and prompts for human decisions, generating text when a draft is missing. `sync --dry-run` previews eligibility; `process --limit 10` explicitly processes a batch under server policy, which defaults to human review.

These workflows use stored source facts. Your source connector owns refresh and scheduling.

## Create and monitor workspaces

Workspaces are optional saved views. Choose **Workspaces → New workspace**, enter a name and record types, then save. **Include all** covers every current and future type. **Add a record type** prepares a view before importing it.

Use matching lowercase `kind` identifiers such as `job_application`, `partnership`, or `project`. Workspaces share engine policy, contacts, model settings, credentials, and outbox. Removing a workspace removes its view, not its data.

**Overview → Choose workspaces** selects several views to monitor, including views on other engines. Checks run every 30 seconds while visible and can be paused. Failures retain marked stale counts. Counts can overlap between views.


## Configure a drafting backend

An engine model is needed only for **Generate draft** or a CLI draft request without supplied text. **Write draft**, agent-supplied text, and review of saved drafts require no engine model.

Under **Setup → Model backend**, choose an OpenAI-compatible API or the optional Codex CLI adapter. Saved settings apply to requests from the browser, CLI, and API.

For an API backend, enter its URL, model name, timeout, and credential environment variable. Set the secret in `RESPAWNED_MODEL_API_KEY` or `LITELLM_MASTER_KEY` on the server and restart. The browser stores the variable name, not the secret. LiteLLM is optional.

For Codex, install and authenticate the runtime on the engine host, select `codex_cli`, and generate a draft to invoke it. Saving settings does not run login or availability probes. The engine validates accepted text and keeps all review checks.

Set the server policy's sender/sign-off before generating copy. Configuration saves do not call the backend.


## Engine configuration

Engine commands do not automatically read `.env`. Keep `DB_*` values and model credentials in the engine's shell or service configuration. For a custom policy, set `RESPAWNED_POLICY_PATH` to a file outside the installation directory. `respawned init` is an optional explicit database/schema check before startup.

Use `respawned ui --port 8001` for another port or `--no-open` to print the browser link. Set `RESPAWNED_API_URL` to that loopback URL in CLI terminals using a custom port. Stop a foreground engine with Ctrl+C; PostgreSQL remains separate.

PostgreSQL holds records, activities, drafts, outbox reservations and receipts, workspaces, and saved model settings. Preserve the engine environment and any custom policy file separately from database backups.

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

`respawned status` reads public health and readiness endpoints without an access credential. `/healthz` reports the engine version and process liveness. `/readyz` checks database, schema, and policy; it does not test a model. The status client needs no database settings.

| Symptom | Check |
| --- | --- |
| Database unavailable | Engine-host `DB_*` values and PostgreSQL readiness |
| CLI cannot connect | Engine process, API URL, and local or remote access |
| Status cannot determine the engine version | Older 2.0 health responses lack version metadata; update and restart the engine |
| Nothing ready for review | **All tracked**, confirmed recipient, status, policy, and cooldown |
| Draft generation fails | Backend, model access, server credential, and timeout |
| Write timed out | Read saved state before repeating the action |
| Remote UI fails | HTTPS URL, engine credential, and exact allowed origin |


## Installation options

To inspect the installer before running it:

```bash
curl -fsS https://respawned.williamshayden.com/install.sh -o install.sh
# Read install.sh, then run it.
sh install.sh
```

The installer verifies the wheel checksum, installs into `~/.local/share/respawned/2.2.0`, and adds `~/.local/bin/respawned`. Use `sh install.sh --prefix /absolute/path` for another prefix. No repository clone or Node.js is needed.

[Download package](/downloads/respawned-2.2.0-py3-none-any.whl) · [Source archive](/downloads/respawned-2.2.0.tar.gz) · [Checksums](/SHA256SUMS) · [Package provenance](/release.json) · [Site manifest](/site-manifest.json) · [Release history](https://github.com/williamshayden/respawned/releases)

## Update the engine and clients

1. Stop the engine before upgrading; use Ctrl+C for a foreground `ui` or `serve` process. Keep PostgreSQL running for backup.
2. Back up the existing database and preserve the engine environment and any custom policy file.
3. Download the current [website installer](/install.sh) again and run it with the same prefix, following [Install and start locally](#install-and-start-locally). A previously downloaded installer remains pinned to its original release. If you installed the wheel directly, update the [2.2.0 wheel](/downloads/respawned-2.2.0-py3-none-any.whl) in that same Python environment.
4. Restart with the usual `ui` or `serve` command and existing database settings. Check readiness, then reload the browser and reconnect clients. Separately installed clients can use `respawned status` to check the running engine.

Use matching 2.2.0 client and engine installations for this release. Status displays and accepts other minor or patch versions within the same major. Update separately installed client environments with the engine; the matching browser UI is bundled with it.

The installer retains earlier version environments and has no uninstall command. To remove an installer-managed copy, stop the engine and remove its `bin/respawned` symlink and `share/respawned` program directory under the chosen prefix, after confirming they belong to this installation. Preserve PostgreSQL, its backups, and external configuration when removing program files.

### Upgrading to 2.0

When upgrading from 1.1 or earlier, workflow CLI commands move to the API. Remove database settings from client-only environments, and configure remote clients with the engine URL and credential. `--now` and `--policy` are server/simulation concerns, not workflow-client options.

Data routes require authentication starting with 2.0. New clients use `/v1/workflow`; existing `/v1/ui` aliases remain available. See the [migration reference](/api/#upgrading-to-20).
