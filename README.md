# Respawned

Track follow-ups, review drafts, and record confirmed sends from your own tools. Respawned supports job applications, sales, and other record types through one workflow.

The browser, CLI, and Python SDK call the same HTTP API. The engine stores records in PostgreSQL and owns policy, validation, review, and outbox state. Your agent can supply draft text or request a configured model.

[Documentation](https://respawned.williamshayden.com/) · [Agent guide](docs/AGENT_INTEGRATION.md) · [Agent prompt](docs/agent-prompt.txt) · [API reference](docs/API.md)

## Install and start

The package includes the CLI, API, SDK, and prebuilt browser UI. The installer needs `curl`, a POSIX shell, and Python 3.12+ with `venv` and `ensurepip`. It supports Linux, macOS, and WSL; release qualification runs on Linux. PostgreSQL is a separate engine requirement.

[View installer](https://respawned.williamshayden.com/install.sh) · [Download package](https://respawned.williamshayden.com/downloads/respawned-2.0.0-py3-none-any.whl) · [Checksums](https://respawned.williamshayden.com/SHA256SUMS)

```sh
curl -fsS https://respawned.williamshayden.com/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

To inspect the script first:

```sh
curl -fsS https://respawned.williamshayden.com/install.sh -o install.sh
# Read install.sh, then run it.
sh install.sh
```

The installer verifies the wheel checksum, installs into `~/.local/share/respawned/2.0.0`, and adds `~/.local/bin/respawned`. It leaves shell startup files unchanged and refuses to overwrite unrelated commands. Use `sh install.sh --prefix /absolute/path` for another prefix.

Create a PostgreSQL database and user on the engine host, then set its connection values:

```sh
export DB_HOST=127.0.0.1
export DB_PORT=5432
export DB_NAME=respawned
export DB_USER=respawned
export DB_PASSWORD='your-database-password'
respawned init
respawned ui
```

The app opens at `http://127.0.0.1:8000` with browser access connected. The launcher also creates private CLI access for other terminals on that machine. No token copying or model credentials are needed to start.

Engine commands do not automatically load `.env`. Keep `DB_*` values and model credentials in the engine's shell or service configuration. For a custom policy, set `RESPAWNED_POLICY_PATH` to a file outside the installation directory. Client-only environments use the engine URL, an access credential or local discovery, and an optional request timeout.

PostgreSQL holds records, activities, drafts, outbox reservations and receipts, workspace definitions, and saved model settings. The installation directory holds program files and dependencies. Preserve the engine environment and any custom policy file separately from database backups.

Use `--port 8001` for another port or `--no-open` to print a browser link. Set `RESPAWNED_API_URL` to the chosen loopback URL in CLI terminals using a custom port. Leave the engine running while using clients. Ctrl+C stops the engine; PostgreSQL remains separate.

## CLI and API

With the local engine running, use another terminal:

```bash
respawned import --file records.json
respawned draft ats:application-123 --body-file draft.txt
respawned review
respawned outbox --pending --json
```

Use the [record contract](docs/API.md#ingest-records) for `records.json` and plain text for `draft.txt`. Supplied copy is validated without configuring an engine model. Omit `--body-file` to request the engine's backend.

`review` evaluates a bounded queue and always asks for human decisions. `sync --dry-run` previews eligibility. `process --limit 10` explicitly processes a batch under the server's review policy, which defaults to human review.

`inbox --json` reads unanswered human replies. `outbox --path outbox.csv` exports a spreadsheet. Reads and exports do not mark messages sent.

For a remote engine, set `RESPAWNED_API_URL` and `RESPAWNED_REVIEW_TOKEN` in the client environment. Workflow commands and `RespawnedClient.from_env()` use the same connection. See [Agent integration](docs/AGENT_INTEGRATION.md) for CLI, SDK, and HTTP examples.

The API reference is available on the engine at `/docs` and `/openapi.json`. Canonical operator routes use `/v1/workflow`. Local `respawned serve` starts the engine without opening a browser; `--api-only` disables the bundled UI.

## Browser workflow

1. **Setup → Records & sources:** import canonical JSON. **Review imported records** opens the queue.
2. **Workspaces:** save views for selected record kinds or include all kinds.
3. **Review queue:** evaluate the queue, inspect evidence, generate or review a draft, then approve it to the outbox.
4. **Overview:** watch several workspaces. **Connections** adds other engines.

**Refresh** reads the current view; **Evaluate queue** applies policy to stored facts. Neither refreshes an external source. **All tracked** includes waiting, closed, and contactless records.

Workspaces share an engine's policy, contacts, credentials, and outbox. Use separate engines for separate data or authority. The [browser guide](docs/WEB_UI.md) covers configuration and remote connections.

## Model backends

Configure **Setup → Model backend** only when the engine should generate new text. Choose an OpenAI-compatible API, an optional LiteLLM proxy, or the optional Codex CLI adapter.

Credentials stay on the engine host. Saving settings does not invoke the backend; explicit generation does. Supplied text and existing drafts need no model configuration. See [backend setup](docs/WEB_UI.md#connect-a-model-backend).

## Outbox integration

A sending service fetches approved messages, sends through its own provider, and records the confirmed result:

- `GET /v1/outbox/pending` reads approved pending messages.
- `GET /v1/outbox/{id}` reads a message and its receipt.
- `POST /v1/outbox/{id}/receipt` records a confirmed send.

Use a dedicated `RESPAWNED_OUTBOX_TOKEN` for the connector. It does not grant drafting or approval authority. Provider idempotency and durable coordination belong to the sender.

The [outbox reference](docs/API.md#outbox-integration) includes request fields and retries. A [standalone Python client](examples/outbox_client.py) needs no third-party packages or source checkout.

## Direct package installation

Install the wheel into an existing Python 3.12+ environment:

```sh
python -m pip install 'https://respawned.williamshayden.com/downloads/respawned-2.0.0-py3-none-any.whl'
```

The [source archive](https://respawned.williamshayden.com/downloads/respawned-2.0.0.tar.gz) also includes the prebuilt UI and connector example. Package installation does not run a frontend build.

## Upgrading to 2.0

1. Stop the engine before upgrading; use Ctrl+C for a foreground `ui` or `serve` process. Keep PostgreSQL running for backup.
2. Back up the existing database and preserve the engine environment and any custom policy file.
3. Download the current website installer again using [Install and start](#install-and-start), then run it with the same prefix. A previously downloaded installer remains pinned to its original release. For a direct Python installation, update the wheel in that same environment using [Direct package installation](#direct-package-installation).
4. On the engine host, check `respawned --version`, run `respawned init` with the existing database settings, and restart with the usual `ui` or `serve` command. Reload the browser and reconnect clients.

Use the 2.0.0 CLI and Python SDK with the 2.0.0 engine; this is the qualified combination. Update separately installed client environments to the same release. The matching browser UI is bundled with the engine.

Workflow commands now call the API and no longer read PostgreSQL settings or accept `--now` and `--policy`. Configure policy on the server and use fixed time only in simulations.

Data routes now require operator authentication. Existing `/v1/ui` aliases remain available; new clients use `/v1/workflow`. Outbox connector routes and receipt semantics are unchanged. See [migration details](docs/API.md#upgrading-to-20).

The installer retains earlier version environments and has no uninstall command. To remove an installer-managed copy, stop the engine and remove its `bin/respawned` symlink and `share/respawned` program directory under the chosen prefix, after confirming they belong to this installation. Preserve PostgreSQL, its backups, and external configuration when removing program files.

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

If renaming a checkout, preserve its Compose project name with `docker compose -p <existing-project>`. The quote-specific prototype schema requires [separate migration](docs/RELEASE_CHECKS.md). Historical evidence retains its original names.

## Documentation and development

| Task | Guide |
| --- | --- |
| Browser setup, workspaces, and remote engines | [Browser guide](docs/WEB_UI.md) |
| Connect an agent | [Agent guide](docs/AGENT_INTEGRATION.md), [prompt](docs/agent-prompt.txt) |
| API schemas, policy, and retries | [API reference](docs/API.md) |
| Demo and recording provenance | [Demo notes](docs/DEMO.md) |
| Build the UI and run browser checks | [Frontend development](docs/WEB_UI.md#frontend-development-and-bundled-assets) |
| Preserve existing data | [Database migration](docs/RELEASE_CHECKS.md) |
| Run isolated fixtures | [Simulations](docs/SIMULATIONS.md), [agent experiments](docs/AGENT_SIMULATIONS.md), [connector simulation](docs/CONNECTOR_SIMULATION.md) |
| Build and verify documentation downloads | [Documentation site](site/README.md) |
| Changes | [Changelog](CHANGELOG.md) |

Run the Python suite with Docker available:

```bash
uv run --frozen pytest -q
uv lock --check
```

With an existing test database, use `--postgres-url <test-url>` and `--ignore=tests/test_app_compose.py`; that omits Docker lifecycle coverage. Tests own isolated schemas. Model calls are stubbed unless a live simulation is explicitly selected.

In `web`, use `npm ci`, `npm test`, `npm run test:e2e`, and `npm run bundle:check`. After UI edits, regenerate and commit packaged assets with `npm run bundle`.

Earlier [design proposals](docs/PROPOSALS.md), [quality review](docs/QUALITY_REVIEW.md), and [pre-UI release record](docs/V1_RELEASE.md) are historical evidence.

Licensed under the [MIT License](LICENSE).
