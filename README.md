# Respawned

Track follow-ups, review drafts, and record confirmed sends from your own tools. Respawned supports job applications, sales, and other record types through one workflow.

An agent or person prepares a draft. A person reviews it and approves it to the unsent outbox. A sending integration delivers the approved text and records the confirmed receipt.

The browser, CLI, and Python SDK call the same HTTP API. The engine owns PostgreSQL storage, policy, validation, review, and outbox state.

[Documentation](https://respawned.williamshayden.com/) · [Agent guide](https://respawned.williamshayden.com/agent-integration/) · [Agent prompt](https://respawned.williamshayden.com/agent-prompt.txt) · [API reference](https://respawned.williamshayden.com/api/)

## Install and start

You need `curl`, a POSIX shell, Python 3.12+ with `venv` and `ensurepip`, and a PostgreSQL database. The installer supports Linux, macOS, and WSL; release qualification runs on Linux.

```sh
curl -fsS https://respawned.williamshayden.com/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

The package includes the CLI, API, SDK, and prebuilt browser UI. [View the installer](https://respawned.williamshayden.com/install.sh) or see [installation options](#installation-options).

Create a PostgreSQL database and user on the engine host, then set their actual connection values and start the app:

```sh
export DB_HOST=127.0.0.1
export DB_PORT=5432
export DB_NAME=respawned
export DB_USER=respawned
export DB_PASSWORD='your-database-password'
respawned ui
```

The engine initializes its schema and checks database and policy readiness. The launcher reports readiness and opens `http://127.0.0.1:8000` with browser access connected. Leave it running while using the app. Startup creates no sample records and needs no model credentials.

## Browser workflow

Start with one real record and your own draft text. No workspace or model setup is required.

1. Open **Setup → Records & sources**. Paste the JSON below or select a JSON file. Replace the example identity, date, and recipient with confirmed source facts, then select **Import records**.
2. Choose **Review imported records**, open the record, and inspect its reason and history. Choose **Write draft**, enter your text, and **Save draft**.
3. A person checks the saved text, recipient, and evidence, then chooses **Approve to outbox**. The approved message is now visible in **Outbox**, still unsent.

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

Save the same payload as `records.json` if using the CLI. Records are complete snapshots; omitted optional fields are cleared. Use stable IDs and actual source timestamps. The [record contract](https://respawned.williamshayden.com/api/#ingest-records) covers activities and other fields.

For `draft.txt` or the browser editor, replace this example with your own copy:

```text
Hi Alex, I am following up on my application for the Backend Engineer role at Example. Is there an update you can share?
```

Saving validates the copy and current eligibility. Approval rechecks the displayed version, recipient, facts, and contact-wide cooldown. If a record is waiting, inspect its reason in **All tracked**; importing facts does not make every record eligible. **Refresh** reads stored state without fetching source updates.

## CLI and API

The local launcher also creates private CLI access for other terminals on that machine. Workflow clients do not need database settings or token copying. Optionally run `respawned status` from the client terminal to check its connection and engine readiness.

An agent prepares the record and draft:

```bash
respawned import --file records.json
respawned draft ats:application-123 --body-file draft.txt
```

Use the successful response to report the saved draft's ID and status. Supplied text needs no engine model. Import only new or changed source facts; no separate queue evaluation is needed to draft a record.

A person then reviews that saved draft:

```bash
respawned review ats:application-123
```

This opens the selected record's current saved draft and prompts for a human decision. It does not generate copy or evaluate the queue. The engine performs the same review checks as the browser.

See [Agent integration](https://respawned.williamshayden.com/agent-integration/) for SDK and HTTP alternatives, uncertain-write recovery, and the downloadable prompt.

## Outbox integration

Approval reserves an unsent message. A sending integration reads approved recipients and exact text, sends through its own provider, then records the confirmed result:

- `GET /v1/outbox/pending` reads approved pending messages.
- `GET /v1/outbox/{id}` reads a message and its receipt.
- `POST /v1/outbox/{id}/receipt` records a confirmed send.

Use a dedicated `RESPAWNED_OUTBOX_TOKEN` for the connector. It grants neither drafting nor approval authority. The sender owns provider idempotency and durable coordination; reconcile an uncertain send with the provider before retrying.

`respawned outbox --pending --json` reads approved exact text. `outbox --path outbox.csv` exports spreadsheet-safe cells. Reads and exports do not mark messages sent.

The [outbox reference](https://respawned.williamshayden.com/api/#outbox-integration) covers fields and retries. A [standalone Python client](https://respawned.williamshayden.com/examples/outbox_client.py) needs no third-party packages.

## Workspaces and other workflows

Workspaces are optional saved views for selected record types. **Overview** monitors several views, and **Connections** adds other engines. Workspaces share an engine's policy, contacts, credentials, and outbox. Use separate engines for separate data or authority.

`respawned inbox --json` reads unanswered human replies. Bare `respawned review` evaluates a bounded queue and prompts for human decisions, generating copy when a draft is missing. `sync --dry-run` previews eligibility. `process --limit 10` explicitly processes a batch under server policy, which defaults to human review. These operations use stored source facts.

The API reference is available on the engine at `/docs` and `/openapi.json`. Canonical operator routes use `/v1/workflow`. See the [browser guide](https://respawned.williamshayden.com/) and [API reference](https://respawned.williamshayden.com/api/).

## Model backends

Configure **Setup → Model backend** only when the engine should generate new text. Choose an OpenAI-compatible API, an optional LiteLLM proxy, or the optional Codex CLI adapter.

Credentials stay on the engine host. Saving settings does not invoke the backend; **Generate draft** or `respawned draft RECORD_ID` without `--body-file` does. Writing your own text and reviewing saved drafts need no model. See [backend setup](https://respawned.williamshayden.com/#configure-a-drafting-backend).

## Engine configuration and remote clients

Engine commands do not automatically load `.env`. Keep `DB_*` values and model credentials in the engine's shell or service configuration. For a custom policy, set `RESPAWNED_POLICY_PATH` to a file outside the installation directory. `respawned init` is an optional explicit database/schema check before startup.

Use `respawned ui --port 8001` for another port or `--no-open` to print a browser link. Set `RESPAWNED_API_URL` to that loopback URL in CLI terminals using a custom port. Local `respawned serve` starts the engine without opening a browser; `--api-only` disables the bundled UI. Ctrl+C stops a foreground engine; PostgreSQL remains separate.

Remote CLI and SDK clients use `RESPAWNED_API_URL` and `RESPAWNED_REVIEW_TOKEN`. The same connection is used by `RespawnedClient.from_env()`. Workflow clients need only the URL, access credential or local discovery, and optional timeout. See [engine access](https://respawned.williamshayden.com/#access-and-draft-version-tokens).

PostgreSQL holds records, activities, drafts, outbox reservations and receipts, workspace definitions, and saved model settings. Preserve the engine environment and any custom policy file separately from database backups.

## Installation options

To inspect the script before running it:

```sh
curl -fsS https://respawned.williamshayden.com/install.sh -o install.sh
# Read install.sh, then run it.
sh install.sh
```

The installer verifies the wheel checksum, installs into `~/.local/share/respawned/2.2.0`, and adds `~/.local/bin/respawned`. It leaves shell startup files unchanged and refuses to overwrite unrelated commands. Use `sh install.sh --prefix /absolute/path` for another prefix.

[Download package](https://respawned.williamshayden.com/downloads/respawned-2.2.0-py3-none-any.whl) · [Checksums](https://respawned.williamshayden.com/SHA256SUMS)

### Direct package installation

Install the wheel into an existing Python 3.12+ environment:

```sh
python -m pip install 'https://respawned.williamshayden.com/downloads/respawned-2.2.0-py3-none-any.whl'
```

The [source archive](https://respawned.williamshayden.com/downloads/respawned-2.2.0.tar.gz) also includes the prebuilt UI and connector example. Package installation does not run a frontend build.

## Upgrading to 2.2

1. Stop the engine before upgrading; use Ctrl+C for a foreground `ui` or `serve` process. Keep PostgreSQL running for backup.
2. Back up the existing database and preserve the engine environment and any custom policy file.
3. Download the current website installer again using [Install and start](#install-and-start), then run it with the same prefix. A previously downloaded installer remains pinned to its original release. For a direct Python installation, update the wheel in that same environment using [Direct package installation](#direct-package-installation).
4. Restart with the usual `ui` or `serve` command and existing database settings. Check readiness, then reload the browser and reconnect clients. Separately installed clients can use `respawned status` to check the running engine.

Use matching 2.2.0 client and engine installations for this release. The browser UI ships with the engine.

The installer retains earlier version environments and has no uninstall command. To remove an installer-managed copy, stop the engine and remove its `bin/respawned` symlink and `share/respawned` program directory under the chosen prefix, after confirming they belong to this installation. Preserve PostgreSQL, its backups, and external configuration when removing program files.

### Upgrading to 2.1

The `status` command introduced in 2.1 reports client and engine versions and readiness. Use `status --json` for scripts or `--version` to read the installed client version offline. Status accepts minor and patch differences within the same major; it does not prove every client/engine pair has been tested. Older 2.0 health responses omit the engine version. See [status and exit codes](https://respawned.williamshayden.com/api/#engine-status-and-versions).

### Upgrading to 2.0

When migrating from 1.1 to 2.0 or later, workflow commands call the API and no longer read PostgreSQL settings or accept `--now` and `--policy`. Configure policy on the server and use fixed time only in simulations.

The 2.0 API introduced operator authentication for data routes. Existing `/v1/ui` aliases remain available; new clients use `/v1/workflow`. Outbox connector routes and receipt semantics are unchanged. See [1.1-to-2.0 migration details](https://respawned.williamshayden.com/api/#upgrading-to-20).

## Developer source checkout

With Python 3.12+, [uv](https://docs.astral.sh/uv/), and Docker Compose:

```bash
git clone https://github.com/williamshayden/respawned.git
cd respawned
cp .env.example .env
uv sync --frozen
```

Set the engine's database values in `.env`. Start the database and application:

```bash
docker compose --profile postgres up -d --wait db
uv run --env-file .env respawned ui
```

For an existing PostgreSQL server, use its credentials and skip the Compose command. In another terminal, workflow commands can run with `uv run respawned ...` and the launcher's local access.

Stop the database with `docker compose --profile postgres down`. Named volumes remain; preserve them when upgrading.

### Run the checkout in Docker

Set `RESPAWNED_REVIEW_TOKEN` in `.env`, then:

```bash
docker compose --profile app up -d --build --wait
curl --fail http://127.0.0.1:8000/readyz
```

Open `http://127.0.0.1:8000` and enter the token under **Setup → Engine access**. Host CLI clients use the same URL and credential through their environment. The image includes the UI; model services remain optional.

`/readyz` checks database, schema, and policy. `/healthz` checks process liveness. Stop services with `docker compose --profile app --profile litellm down`, preserving volumes.

### Upgrade from Follow-up Engine

The package, imports, and CLI are `respawned`. Project-specific `FUE_` environment variables now use `RESPAWNED_`. Keep existing database names, credentials, and storage.

If renaming a checkout, preserve its Compose project name with `docker compose -p <existing-project>`. The quote-specific prototype schema requires [separate migration](https://github.com/williamshayden/respawned/blob/main/docs/RELEASE_CHECKS.md). Historical evidence retains its original names.

## Documentation and development

| Task | Guide |
| --- | --- |
| Browser setup, workspaces, and remote engines | [Browser guide](https://respawned.williamshayden.com/) |
| Connect an agent | [Agent guide](https://respawned.williamshayden.com/agent-integration/), [prompt](https://respawned.williamshayden.com/agent-prompt.txt) |
| API schemas, policy, and retries | [API reference](https://respawned.williamshayden.com/api/) |
| Demo and recording provenance | [Demo notes](https://github.com/williamshayden/respawned/blob/main/docs/DEMO.md) |
| Build the UI and run browser checks | [Frontend development](https://github.com/williamshayden/respawned/blob/main/docs/WEB_UI.md#frontend-development-and-bundled-assets) |
| Preserve existing data | [Database migration](https://github.com/williamshayden/respawned/blob/main/docs/RELEASE_CHECKS.md) |
| Run isolated fixtures | [Simulations](https://github.com/williamshayden/respawned/blob/main/docs/SIMULATIONS.md), [agent experiments](https://github.com/williamshayden/respawned/blob/main/docs/AGENT_SIMULATIONS.md), [connector simulation](https://github.com/williamshayden/respawned/blob/main/docs/CONNECTOR_SIMULATION.md) |
| Build and verify documentation downloads | [Documentation site](https://github.com/williamshayden/respawned/blob/main/site/README.md) |
| Changes | [Changelog](https://github.com/williamshayden/respawned/blob/main/CHANGELOG.md) |

Run the Python suite with Docker available:

```bash
uv run --frozen pytest -q
uv lock --check
```

With an existing test database, use `--postgres-url <test-url>` and `--ignore=tests/test_app_compose.py`; that omits Docker lifecycle coverage. Tests own isolated schemas. Model calls are stubbed unless a live simulation is explicitly selected.

In `web`, use `npm ci`, `npm test`, `npm run test:e2e`, and `npm run bundle:check`. After UI edits, regenerate and commit packaged assets with `npm run bundle`.

Earlier [design proposals](https://github.com/williamshayden/respawned/blob/8b20e3b75dfd750483c31f192a17faf665bb6ee9/docs/PROPOSALS.md), [quality review](https://github.com/williamshayden/respawned/blob/8b20e3b75dfd750483c31f192a17faf665bb6ee9/docs/QUALITY_REVIEW.md), and [pre-UI release record](https://github.com/williamshayden/respawned/blob/8b20e3b75dfd750483c31f192a17faf665bb6ee9/docs/V1_RELEASE.md) are historical evidence.

Licensed under the [MIT License](https://github.com/williamshayden/respawned/blob/main/LICENSE).
