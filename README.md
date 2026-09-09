# Respawned

Respawned organizes follow-up work, drafts messages, and keeps an unsent outbox.
It uses one interface for job applications, sales, and other record types. Track
work before a recipient is known, create your own workspaces, and monitor several
local or remote engines from the same UI.

The browser and CLI use the same Python application services. PostgreSQL stores
records, policy decisions, drafts, and approvals; a model writes copy only when
requested. The built UI ships in the Python wheel, source distribution, and Docker
image. Node.js is needed only for frontend development.

**V1 is for a trusted operator.** Source integrations submit canonical records;
Respawned does not connect a mailbox, schedule source updates, or send messages.
Some legacy API routes are unauthenticated, so use loopback or a trusted network.
Workspaces are views within an engine, not separate accounts.

## Quickstart

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), and PostgreSQL.
The commands below start PostgreSQL with Docker Compose v2; an existing database
also works. The qualified environment is Linux, including WSL2 with Docker Desktop.
No model credentials are needed to start, import records, or inspect work.

From the repository root:

```bash
cp .env.example .env
uv sync --frozen
```

In PowerShell, use `Copy-Item .env.example .env`. Edit the database values in
`.env`, including `DB_PASSWORD`. For an existing database, use its current
credentials, database name, and port. Before recreating an older Compose
installation, follow the [database migration guide](docs/RELEASE_CHECKS.md).

Start only the database, then launch the local app:

```bash
docker compose --profile postgres up -d --wait db
uv run --env-file .env respawned ui
```

`respawned ui` opens the bundled app at <http://127.0.0.1:8000> with review access
connected. No manual token is needed. Use `--port 8001` if the port is occupied,
or `--no-open` to print a one-use launch link. The session survives reloads for
12 hours, until you lock access or stop the server. Start it again for a new link.
Normal startup creates no sample records.

1. **Setup:** import canonical JSON from your source and choose a model backend
   when you want to generate drafts. Importing and saving settings make no model
   requests. See the [input contract](docs/API.md#ingest-records).
2. **Workspaces:** create a named view for any record kinds, or include all kinds.
3. **Review queue:** inspect the reason and history, generate a draft, edit it,
   then approve it to the unsent outbox. **All tracked** includes waiting, closed,
   and contactless records too.
4. **Overview:** choose several workspaces to watch. **Connections** adds another
   engine by URL and its own review token; see [remote setup](docs/WEB_UI.md#monitor-local-and-remote-workspaces).

Approval and export do not deliver a message. See the [outbox workflow](docs/WEB_UI.md#import-records-and-use-the-outbox)
for recording actual outbound activity after manual delivery.

Stop the CLI with Ctrl+C. Stop the database with
`docker compose --profile postgres down`; named volumes remain. Do not add
`--volumes` when shutting down an installation you intend to keep.

### Run the application in Docker instead

Choose this instead of the local `respawned ui` process; both default to port
8000. In `.env`, set `RESPAWNED_REVIEW_TOKEN` to a random operator password. You
can generate a value with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Then:

```bash
docker compose --profile app up -d --build --wait
curl --fail http://127.0.0.1:8000/readyz
```

Open <http://127.0.0.1:8000> and enter that token in **Setup → Review access**.
Manual tokens stay in tab memory and clear on reload. An empty review token
keeps the protected UI operations disabled in ordinary server mode.
The image already contains the UI. The optional model service is separate.

`/readyz` checks the database, schema, and policy; `/healthz` checks process
liveness. Neither proves model availability or fresh source data. Stop services
with `docker compose --profile app --profile litellm down`, preserving volumes.

## Model backends

In **Setup → Model backend**, configure your chosen drafting service. The default
adapter accepts an OpenAI-compatible API URL, model or proxy alias, timeout, and
credential environment variable. LiteLLM is optional; the `litellm` Compose profile
supplies a proxy after its upstream model and key are configured in `.env`.

Saved model settings apply to the browser, CLI, and API on that engine. Credentials
remain on the server. Saving validates configuration only; it does not test a
provider, executable, or login. A backend is invoked when you explicitly generate
a draft. No particular model provider or CLI login is a product or release
requirement. See [backend configuration](docs/WEB_UI.md#connect-a-model-backend)
for the available adapters, including the optional experimental CLI adapter.

## CLI and API

Use the same database environment as the app. The CLI does not load `.env`
automatically; `uv run --env-file .env` does that in these checkout examples:

```bash
uv run --env-file .env respawned sync --dry-run
uv run --env-file .env respawned sync --limit 10
uv run --env-file .env respawned review
uv run --env-file .env respawned inbox --json
uv run --env-file .env respawned outbox --path exports/outbox.csv
```

`sync` publishes eligible candidates without drafting. `review` generates missing
drafts as needed and supports approve, reject, edit, and skip; human review is the
default. `inbox` reads unanswered human replies independently of outreach cooldown.
`outbox` exports reservations without changing them. Use `respawned COMMAND --help`
for options, and the [API and policy guide](docs/API.md) for import, processing,
review versions, pagination, and retry behavior.

For an API server without the browser interface:

```bash
uv run --env-file .env respawned serve --api-only
```

Interactive endpoint schemas are at <http://127.0.0.1:8000/docs>. Protected review
routes use `RESPAWNED_REVIEW_TOKEN`; the separate `RESPAWNED_PROCESS_TOKEN` enables
policy-controlled processing and does not grant human approval authority.

## Install a built artifact

With Python 3.12+, install a downloaded or locally built wheel into a virtual
environment, set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`, then
run the app:

```bash
python -m venv .venv
# Activate .venv for your shell before the following commands.
python -m pip install /path/to/respawned-1.0.0-py3-none-any.whl
respawned init
respawned ui
```

The source distribution also includes prebuilt assets; installation does not run
npm. Package version `1.0.0` is not a claim that an artifact has been published.
See [integration qualification](docs/INTEGRATION_REVIEW.md) for recorded checks.

### Upgrade from Follow-up Engine

The Python package, imports, and CLI are now `respawned`; project-specific `FUE_`
environment variables use `RESPAWNED_`. Reinstall from this checkout with
`uv sync --frozen`. Keep existing database names, credentials, and volumes. If
renaming a checkout directory, preserve its Compose project name with
`docker compose -p <existing-project>` so it keeps the same storage.

Back up before upgrading, run `respawned init`, then `respawned sync` to publish a
fresh queue. Canonical upgrades add schema fields without rewriting old messages;
the quote-specific prototype requires [separate migration](docs/RELEASE_CHECKS.md).
The GitHub repository URL and historical evidence names retain their old spelling.

## Documentation and development

| Task | Guide |
| --- | --- |
| Workspaces, local access, remote engines, models, and outbox | [Browser guide](docs/WEB_UI.md) |
| Source adapters, API operations, policy, and retries | [API and policy](docs/API.md) |
| Build the UI and run browser checks | [Frontend development](docs/WEB_UI.md#frontend-development-and-bundled-assets) |
| Preserve an existing database | [Database migration](docs/RELEASE_CHECKS.md) |
| Review implementation evidence and remaining integration gaps | [Integration review](docs/INTEGRATION_REVIEW.md) |
| Run isolated synthetic workflows | [Scripted simulations](docs/SIMULATIONS.md), [agent experiments](docs/AGENT_SIMULATIONS.md), [HTTP connector simulation](docs/CONNECTOR_SIMULATION.md) |
| Changes in this version | [Changelog](CHANGELOG.md) |

Run the Python suite with Docker available:

```bash
uv run --frozen pytest -q --capture=no
uv lock --check
```

With an existing test PostgreSQL server, use `--postgres-url <test-url>` and
`--ignore=tests/test_app_compose.py`; this skips Docker lifecycle coverage.
Tests create and remove isolated schemas. Model calls are stubbed unless a live
simulation is explicitly selected. In `web`, use `npm ci`, `npm test`,
`npm run test:e2e`, and `npm run bundle:check`. After UI edits, run `npm run bundle`
and commit the generated package assets with the source change.

Earlier [design proposals](docs/PROPOSALS.md), [handoff](docs/HANDOFF.md),
[quality review](docs/QUALITY_REVIEW.md), and [pre-UI release record](docs/V1_RELEASE.md)
are historical evidence, not the current feature list. Outstanding product work
includes provider connectors, ordered source snapshots and freshness, unresolved
source associations, and delivery claims/results. Public or multi-user hosting
also requires authentication across all routes and an explicit tenant boundary.

Licensed under the [MIT License](LICENSE).
