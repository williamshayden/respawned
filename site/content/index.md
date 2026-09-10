# Documentation

Respawned organizes follow-up work across job applications, sales, and other record types. Review and approve drafts in the browser or CLI, then collect approved messages from the outbox as JSON or CSV. Monetary values are optional.

The browser, CLI, and HTTP API use the same Python application and PostgreSQL database. The browser UI is included in the Python package and Docker image. Node.js is only needed for frontend development.

See [Agent integration](./agent-integration/) for a complete workflow and the [API reference](/api/) for endpoints, authentication, and request formats.

V1 is intended for a trusted operator on loopback or a trusted network. It does not connect a mailbox, schedule source updates, or deliver messages. Some legacy API routes are unauthenticated; workspaces do not provide account or tenant isolation.

## Install and start locally

The installer requires `curl`, a POSIX shell, and Python 3.12+ with `venv` and `ensurepip`. It accepts Linux, macOS, or WSL; the application is qualified on Linux, including WSL2. PostgreSQL remains a separate requirement.

[View installer](/install.sh) · [Download package (Python wheel)](/downloads/respawned-1.1.0-py3-none-any.whl) · [Checksums](/SHA256SUMS)

Install Respawned:

```bash
curl -fsS https://respawned.williamshayden.com/install.sh | sh
```

The installer verifies the pinned wheel's SHA-256 digest, installs it in `~/.local/share/respawned/1.1.0`, and adds a launcher at `~/.local/bin/respawned`. The package includes the CLI, HTTP API, and built browser UI. No Node.js, npm, or repository clone is needed.

A [source archive](/downloads/respawned-1.1.0.tar.gz) and [package provenance](/release.json) are also available. The package provenance identifies the verified application build. Documentation and media can receive updates independently; their source revision is recorded in the [site manifest](/site-manifest.json).

To download and inspect the script before running it:

```bash
curl -fsS \
  https://respawned.williamshayden.com/install.sh -o install.sh
# Read install.sh, then run it.
sh install.sh
```

Use `sh install.sh --prefix /absolute/path` for another installation prefix. Add the selected prefix's `bin` directory to `PATH` if needed. With the default prefix, use:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Configure PostgreSQL

Provide a PostgreSQL database and a role allowed to create and update its application tables. Set the following values in the shell that runs Respawned, replacing the example database credentials:

```bash
export DB_HOST=127.0.0.1
export DB_PORT=5432
export DB_NAME=respawned
export DB_USER=respawned
export DB_PASSWORD='replace-with-your-database-password'
```

Use the same environment for the server and CLI. The CLI does not automatically load a `.env` file, and shell exports do not carry into a new terminal. Store persistent settings using your shell or service configuration. Back up an existing database before upgrading; do not replace its storage or PostgreSQL major version as part of an application install.

After installing the package and configuring the database:

```bash
respawned init
respawned ui
```

`init` initializes or updates the schema. The app opens at `http://127.0.0.1:8000` with local review access connected. No manual review token or model credentials are needed to start, import records, or inspect work. Startup creates no sample records.

Use `respawned ui --port 8001` if the default port is occupied, or `respawned ui --no-open` to print a one-use browser link. Stop the app with Ctrl+C; this does not stop or delete PostgreSQL.

## Import source records

Open **Setup → Records & sources**. Choose a JSON file or paste a payload, review the item counts, then select **Import records**. The UI accepts up to 1,000 combined opportunities and activities, within 2 MB.

This example tracks an application before a human recipient is known. Replace the sample IDs, timestamps, and facts with source data:

```json
{
  "opportunities": [
    {
      "id": "ats:application-123",
      "kind": "job_application",
      "title": "Backend Engineer at Example",
      "status": "open",
      "created_at": "2026-09-01T15:00:00Z",
      "context": {
        "company": "Example",
        "role": "Backend Engineer",
        "stage": "Applied",
        "summary": "Application received; no human contact is known."
      }
    }
  ],
  "activities": [
    {
      "id": "mail:receipt-456",
      "opportunity_id": "ats:application-123",
      "type": "application_receipt",
      "occurred_at": "2026-09-01T15:01:00Z",
      "channel": "email",
      "direction": "inbound",
      "classification": "automated",
      "summary": "Automated application confirmation."
    }
  ]
}
```

A contactless record appears in **All tracked** but cannot become a drafting candidate. After confirming a recipient, import a complete replacement record containing a stable `contact_key` and at least one of `contact_email` or `contact_phone`. Do not treat a no-reply receipt address as a human recipient.

Record IDs remain stable across imports. Opportunities are complete snapshots: omitting an optional field clears it. Activities are immutable: replaying identical IDs and contents is safe; changing an existing activity or referencing a missing record returns a conflict and rolls back the batch. Timestamps require a UTC offset. Extra fields are rejected.

For an adapter on a trusted local network, save the payload as `records.json` and use:

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/ingest \
  -H 'Content-Type: application/json' --data-binary @records.json
```

The protected browser equivalent is `POST /v1/ui/import`. There is no canonical JSON ingest CLI command. A connector owns source credentials, mapping, pagination, and scheduling. Serialize record updates because the engine has no source-revision check. Import does not fetch further data, generate copy, or establish source freshness.

## Create and monitor workspaces

1. Open **Workspaces → New workspace**. Enter a name, optional description, and record types.
2. Choose **Include all** for every current and future kind, or select specific kinds. **Add a record type** prepares a view before records of that kind exist.
3. Select **Save workspace**, then open it to review its records.

Use matching lowercase `kind` identifiers in imports, such as `job_application`, `partnership`, or `project`. Identifiers start with a letter and contain up to 64 letters, digits, or underscores. No domain workspaces are predefined.

Workspaces are saved views of one engine's data. They share contacts, model settings, policy, and outbox; contact-wide cooldowns still apply across views. Removing a workspace removes its definition, not the underlying records or drafts.

**Ready for review** shows eligible action items. **All tracked** also includes waiting, closed, and contactless records. **Overview → Choose workspaces** selects several views to monitor, including views from different engine connections.

Overview checks each engine every 30 seconds while visible. You can pause it or refresh manually. Failed checks retain clearly marked stale counts rather than displaying zero. These checks only read state; they do not sync candidates, import source updates, or generate drafts. Counts can overlap between views, so do not add them as unique totals. A connected engine's source freshness is still unknown.

## Configure a drafting backend

Under **Setup → Model backend**, configure an OpenAI-compatible API endpoint, model name or proxy alias, timeout, and server credential variable. The endpoint must be reachable from the server, with `/v1` included when required by that service.

Set the secret in `RESPAWNED_MODEL_API_KEY` or `LITELLM_MASTER_KEY` on the server and restart it, then select that variable in Setup. The browser stores the variable name, not the secret. For an unauthenticated local endpoint, the selected variable still needs a nonempty local placeholder. LiteLLM is an optional proxy, not a requirement.

Saved non-secret settings apply to browser drafting, CLI review, and API processing on that engine. **Configured** confirms settings only: saving does not test a provider, executable, login, or inference. Explicitly generating a draft invokes the backend. Reading records and reviewing an existing draft need no working model.

No particular provider or CLI login is required. An existing experimental CLI adapter is optional; it is not part of the standard setup path. The engine validates generated and edited copy regardless of backend.

## Review and export

Open a record in **Review queue** and inspect its reason, facts, and history. When eligible, generate a draft, edit and save it if needed, then choose **Approve to outbox**. The server rechecks the displayed version, recipient, current record state, and contact-wide cooldown before reserving the message. Reopen the current draft if another tab changed it.

The default policy requires human review. Policy controls eligibility, ranking, cooldowns, copy limits, and sender/sign-off; the UI displays policy but does not edit it. Configure the sender before drafting. Explicit automatic processing can be enabled by server policy, but it still does not send messages.

The same workflow is available from the CLI, using the app's database environment:

```bash
respawned sync --dry-run
respawned sync --limit 10
respawned review
respawned inbox --json
respawned outbox --path exports/outbox.csv
```

`sync` reevaluates imported facts and publishes candidates without drafting. `review` generates missing drafts on demand and offers approve, reject, edit, and skip. `inbox` reads unanswered human replies independently of outreach cooldown. Workspace and model settings use API operations without dedicated CLI subcommands; the UI calls those same services directly.

**Outbox** downloads CSV. The authenticated HTTP export also supports JSON. CLI and browser exports use the same formatter; CSV escapes formula-like cells for spreadsheet safety, while JSON preserves original strings. Export is read-only and may be repeated.

**Connect a sending tool:** in 1.1.0, an agent or connector can fetch approved messages from `GET /v1/outbox/pending` and record confirmed sends through `POST /v1/outbox/{id}/receipt`. The outbox status and related outbound history update together. [Outbox integration](/api/#outbox-integration) covers credentials, fields, and retries.

Approval and export do not send messages. Your tool handles delivery through its provider; Respawned records the confirmed result. Configure a dedicated `RESPAWNED_OUTBOX_TOKEN` on the server and connector. The token does not grant drafting or approval authority.

## Access and draft version tokens

| Mechanism | Purpose |
| --- | --- |
| Local `respawned ui` session | Opens the local browser with review access; no manual password needed |
| `RESPAWNED_REVIEW_TOKEN` | Shared operator password for ordinary server mode, remote engines, and protected review API requests |
| Draft `review_token` | Automatic version fingerprint submitted with edits and approvals; not a password |
| `RESPAWNED_PROCESS_TOKEN` | Separate credential for policy-controlled `POST /v1/process`; does not grant human review authority |

The launcher's one-use link expires after five minutes. It is exchanged for an HttpOnly, SameSite=Strict browser cookie lasting up to 12 hours, until **Lock access** or server shutdown. Restart `respawned ui` for a fresh link.

For `respawned serve`, Docker, or remote access, generate an operator password:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set it as `RESPAWNED_REVIEW_TOKEN` in the server environment and restart. Enter it in **Setup → Review access**, or send it as `Authorization: Bearer <token>` from an API client. URL-safe random text is convenient; no special password format is required. Manual browser tokens remain in tab memory and clear on reload. Changing the environment value revokes that Bearer credential after restart.

## Connect another engine

Run the other engine in ordinary server mode with its own database environment and review token. For example, behind a trusted proxy or loopback tunnel:

```bash
respawned serve --host 127.0.0.1 --port 8001
```

On that engine, set `RESPAWNED_UI_ORIGINS` to the exact origin hosting the browser app, then restart. For a browser app running locally on the default port:

```dotenv
RESPAWNED_UI_ORIGINS=http://127.0.0.1:8000
```

Multiple origins are comma-separated, with no paths, trailing slashes, or wildcards. `localhost` and `127.0.0.1` differ. **Connections → Prepare a remote engine** shows the actual browser origin. This documentation site's origin is not the application origin.

In **Connections**, enter the engine's name, API root URL, and its review token, then select **Connect engine**. Use **Overview → Choose workspaces** to watch its views, or **Open engine** to work there. Names, URLs, and watch choices persist in this browser; manual tokens do not.

Remote URLs require HTTPS; HTTP is accepted only for loopback addresses. The browser sends only the selected engine's Bearer token, omits remote cookies, and rejects redirects. Local launcher sessions cannot be forwarded to another engine.

The origin allowlist enables browser access; it does not secure unauthenticated routes or add tenant isolation. Protect the whole engine with a trusted network or authenticated reverse proxy. The built-in access model is insufficient for public multi-user hosting.

## API reference and troubleshooting

The [API reference](/api/) covers endpoints, authentication, request formats, and retry behavior. The running server also exposes interactive schemas at `http://127.0.0.1:8000/docs` and machine-readable schemas at `/openapi.json`. Use `respawned serve --api-only` to serve the API without the bundled interface. `/healthz` checks process liveness; `/readyz` checks the database, schema, and policy. Neither proves model availability or fresh source data.

| Symptom | Next check |
| --- | --- |
| Setup opens but the database is unavailable | Verify the server's `DB_*` environment and PostgreSQL readiness; the CLI does not automatically read `.env`. |
| No records are ready | Check **All tracked**, recipient availability, status, policy, and recent contact/outbox cooldown. Refreshing the queue does not fetch external updates. |
| Model is configured but drafting fails | Check the server endpoint, model access, credential variable, and timeout. Existing drafts remain reviewable. |
| A save or import times out | The write may have completed. Reconnect and inspect saved state before retrying; the browser does not automatically repeat writes. |
| Remote connection fails | Check the URL, HTTPS certificate, network path, engine review token, and exact `RESPAWNED_UI_ORIGINS` value. |
| Local review access expires | Restart `respawned ui` and use the new launch link. |
