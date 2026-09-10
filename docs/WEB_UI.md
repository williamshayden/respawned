# Browser guide

The browser is a client of Respawned's HTTP API. The CLI and Python SDK use the same endpoints. Records, workspaces, settings, drafts, and outbox status live on the engine.

The built UI ships with the Python package and Docker image. Running it needs no Node.js or frontend build.

## Start the application

Follow the [installation guide](../README.md#install-and-start) to configure PostgreSQL on the engine host, then run:

```bash
respawned ui
```

The app opens at `http://127.0.0.1:8000` with local browser access connected. The launcher also creates private CLI access for other terminals on that machine. Workflow clients do not need the database environment.

Use `--port 8001` for another port or `--no-open` to print the browser link. For CLI access on another port, set `RESPAWNED_API_URL` to that engine's loopback URL; its private credential is still discovered automatically. Leave the engine running while using clients. Startup creates no sample records.

Setup provides **Go to import** and **Open review queue**. Import records first; configure a model when you want the engine to generate new copy.

### Server and API access

Local `respawned ui` creates a one-use browser link, valid for five minutes. The browser exchanges it for a private session lasting up to 12 hours, until **Lock access**, or until the server stops. Restart the launcher for a fresh link. Browser locking does not revoke the separate local CLI connection.

To start the local engine without opening a browser, use `respawned serve` on its default loopback address. When no operator token is configured, it creates the same private CLI access without opening a browser.

For remote access, configure `RESPAWNED_REVIEW_TOKEN` on the server and run ordinary server mode behind HTTPS. For example:

```bash
respawned serve --host 0.0.0.0 --port 8000
```

Remote exposure requires an operator credential. Enter it under **Setup → Engine access**, in the **Engine access token** field. Scripts use it as a Bearer credential; remote CLI clients set `RESPAWNED_API_URL` and `RESPAWNED_REVIEW_TOKEN`.

Manually entered browser tokens stay in tab memory and clear on reload. Server credential changes take effect after restart. The [API access reference](API.md#api-access) lists operator, processing, and outbox credentials.

## Import records and use the outbox

Open **Setup → Records & sources**, choose a JSON file or paste a payload, and select **Import records**. The preview shows the number of records and activities. Import accepts up to 1,000 combined items within 2 MB.

Use stable source IDs and the [record contract](API.md#ingest-records). Records are complete snapshots, so omitted optional fields are cleared. Activities are immutable: exact replays are accepted and changed content under an existing ID conflicts.

After import, choose **Review imported records**. In the queue, **Evaluate queue** applies policy to the saved facts. **Refresh** only reloads the current view. Neither action fetches an external source; evaluation does not generate copy.

The queue offers **Import records** when empty, **Clear filters** when a filter hides all records, and **View all tracked** when records are waiting. **All tracked** includes waiting, closed, and contactless records. A record needs a confirmed human recipient before it can become an outreach candidate.

Open a record and inspect its reason and history. Use **View message** or **View activity** to read available source text, then generate a draft or review one supplied by an agent. Edit and save the text as needed, then choose **Approve to outbox**. The server checks the displayed version, recipient, current facts, and cooldown. Stale edits or approvals require reopening the current draft.

The outbox shows approved messages and their recorded send status. Download CSV for a spreadsheet or use the [outbox API](API.md#outbox-integration) from a sending integration. JSON preserves exact message text; CSV protects formula-like cells.

Under **Setup → Outbox & delivery**, the UI shows the connector routes and whether `RESPAWNED_OUTBOX_TOKEN` is configured. Credentials stay on the server and in the connector. Your service handles sending, then records a confirmed receipt; the same status appears in the browser, CLI, and API.

## Configure workspaces

Choose **Workspaces → New workspace**. Enter a name, optional description, and the record types to include. **Include all** covers current and future types; **Add a record type** prepares a view before importing that type.

Use matching lowercase `kind` identifiers in imports, such as `job_application`, `partnership`, or `project`. Identifiers start with a letter and allow up to 64 letters, digits, and underscores.

Saving persists the workspace on the engine. Removing it removes the view, not its records or drafts. Workspaces share engine policy, contacts, credentials, model settings, and outbox. Contact-wide cooldowns apply across views.

## Monitor local and remote workspaces

Open **Overview → Choose workspaces** to watch several saved views, including views on different engines. **Open workspace** selects that engine and opens its review interface.

Overview checks connected engines every 30 seconds while visible. Pause it or refresh manually. Failed checks retain marked stale counts. These reads do not import records, evaluate a queue, or invoke a model.

| Count | Meaning |
| --- | --- |
| Tracked records | Matching records, including closed and contactless records |
| Ready for review | Eligible contact groups whose primary record matches the view |
| Pending drafts | Saved pending drafts referencing the view |
| Replies waiting | Unanswered human replies, independent of outreach cooldown |
| Unsent in outbox | Pending approved reservations referencing the view |

Counts describe different stages and overlapping views, so they should not be added as unique totals.

### Add an engine connection

1. Run the other engine with its own database and `RESPAWNED_REVIEW_TOKEN` behind HTTPS, or through a loopback tunnel.
2. On that engine, set `RESPAWNED_UI_ORIGINS` to the exact origin hosting this browser UI and restart. **Connections → Prepare a remote engine** shows the needed origin.
3. In **Connections**, enter its name, API root URL, and engine access token, then choose **Connect engine**.
4. Select workspaces in Overview or choose **Open engine**.

For a browser UI at the default local URL:

```dotenv
RESPAWNED_UI_ORIGINS=http://127.0.0.1:8000
```

Comma-separate multiple exact origins. Omit paths, trailing slashes, and wildcards. `localhost` and `127.0.0.1` are different origins. The documentation website is not the application origin.

Remote URLs require HTTPS; HTTP is accepted for loopback addresses only. The browser sends each engine only its own credential, omits remote cookies, and refuses redirects. Local launch sessions cannot be forwarded.

Names, URLs, and watched workspaces persist in the browser; manual tokens do not. **Unlock** reconnects after reload. **Remove** forgets the connection without deleting server data. Unsaved draft edits hold the current engine until resolved.

The origin allowlist permits browser requests; engine credentials authenticate them. Separate engines own separate data and settings. Workspaces within an engine remain shared views.

## Connect a model backend

Use **Setup → Model backend** when you want the engine to generate copy. Saved non-secret settings apply to browser drafting, CLI requests, and API processing. Saving validates configuration without invoking the backend.

An agent can instead [submit its own draft text](AGENT_INTEGRATION.md). Supplied text and existing drafts require no engine model. Both supplied and generated copy pass the same validation.

Set the server policy's sender/sign-off before generating copy.

### OpenAI-compatible API or LiteLLM

Enter the API base URL, model name or proxy alias, timeout, and server credential variable. Include `/v1` in the URL when required by the provider.

Set the secret in `RESPAWNED_MODEL_API_KEY` or `LITELLM_MASTER_KEY` on the server, restart, and select that variable in Setup. The browser stores its name, not the secret. An unauthenticated local endpoint still needs a nonempty placeholder in the selected variable.

LiteLLM is optional. For the Compose proxy, configure its upstream model and key, then run `docker compose --profile litellm up -d --wait`. A container's `localhost` refers to that container; use an endpoint reachable from the engine.

### Codex CLI

Select `codex_cli` for the optional CLI adapter. Install and authenticate the runtime on the engine host. Saving settings does not run login or availability checks; explicit draft generation invokes `codex exec`.

Server environment options are `RESPAWNED_CODEX_BIN` for the executable, `RESPAWNED_CODEX_SCRATCH_DIR` for an existing scratch directory, `RESPAWNED_CODEX_MODEL`, and `RESPAWNED_CODEX_TIMEOUT_SECONDS` (default 120, maximum 300). Without saved settings, `RESPAWNED_MODEL_BACKEND=codex_cli` selects this adapter.

Windows executables launched from WSL need scratch space on a mounted Windows drive. The standard application image does not include the CLI or host authentication.

The adapter requests structured output with tools disabled. Failed runs, invalid output, and timeouts reject the draft. The engine retains all validation and review checks. See the [adapter experiments](AGENT_SIMULATIONS.md) for earlier qualification evidence.

## Shared application boundary

The browser keeps selection, filters, and unsaved text locally. Workflow decisions and writes go through the API.

| Action | Canonical endpoint | CLI |
| --- | --- | --- |
| Import facts | `POST /v1/workflow/import` | `import --file` |
| Evaluate candidates | `POST /v1/workflow/sync` | `sync` |
| Read review queue | `GET /v1/workflow/queue` | Used by `review` |
| Generate or supply text | `POST /v1/workflow/records/{id}/draft` | `draft` |
| Edit, approve, reject | `POST /v1/workflow/drafts/{id}/…` | `review` |
| Read replies | `GET /v1/workflow/inbox` | `inbox` |
| Read/export outbox | `/v1/workflow/outbox` and `/outbox/export` | `outbox` |
| Process under policy | `POST /v1/process` | `process` |

Workspaces, model settings, and overview also use `/v1/workflow`. Existing `/v1/ui` routes remain compatibility aliases. See [2.0 migration guidance](API.md#upgrading-to-20) for older clients.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Database unavailable | Engine-host `DB_*` settings and PostgreSQL readiness |
| CLI cannot connect | Engine process, selected API URL, and local or remote access |
| Browser access expired | Restart `respawned ui` for a new launch link, or re-enter the remote credential |
| No follow-ups ready | **All tracked**, recipient, status, policy, and recent cooldown |
| Drafting fails | Selected backend, model access, server credentials, and timeout |
| Write timed out | Reload persisted state before repeating the action |
| Remote browser connection fails | HTTPS URL, engine credential, and exact `RESPAWNED_UI_ORIGINS` value |

## Run the connected simulation

For a disposable fixture session, the repository harness runs the real API and PostgreSQL with synthetic records and stubbed drafting. Supply a loopback test database whose role can create schemas:

```bash
uv run python scripts/serve_ui_simulation.py \
  --postgres-url 'postgresql+psycopg2://user:password@127.0.0.1:5432/test_database' \
  --port 8001
```

Open the printed URL and enter the simulation-only token under **Setup → Engine access**. The harness owns an isolated schema and removes it on normal shutdown. Use `--empty` to start without records. This fixture harness does not contact a model or sending provider. The [demo notes](DEMO.md) describe the recorded walkthrough separately.

## Frontend development and bundled assets

From a source checkout, use the pinned Node.js version from CI and run these commands in `web`:

```bash
npm ci
RESPAWNED_API_URL=http://127.0.0.1:8000 npm run dev
```

Vite opens `http://127.0.0.1:5173` and proxies API requests to the selected engine. Use server mode with `RESPAWNED_REVIEW_TOKEN` for a separate development frontend; local launcher browser sessions belong to their own origin.

After changing UI source, rebuild the assets shipped in the Python package:

```bash
npm run bundle
npm run bundle:check
```

Commit the generated `src/respawned/web_assets` files with the source change. The bundle includes third-party notices and a source manifest. Package installation uses these prebuilt files and does not run Node.js.

To serve another frontend build, set `RESPAWNED_UI_DIST` on the engine to an absolute directory containing `index.html`. Set it to `off` to disable the UI; unset selects the bundled assets. `npm run preview` is a static preview on port 4173 and does not use the development API proxy.

## Checks

From `web`:

```bash
npm run bundle:check
npm test
npx playwright install --with-deps chromium
npm run test:e2e
```

Ordinary browser tests use controlled HTTP fixtures. For the connected PostgreSQL test, start a fresh empty simulation from the repository root:

```bash
uv run python scripts/serve_ui_simulation.py --empty --port 8001 \
  --postgres-url 'postgresql+psycopg2://user:password@127.0.0.1:5432/test_database'
```

Then run from `web`:

```bash
RESPAWNED_LIVE_UI_TEST=1 RESPAWNED_LIVE_UI_URL=http://127.0.0.1:8001 \
  npm run test:e2e -- tests/live-ui.spec.ts
```

The test imports records, creates a workspace, reviews a draft, and checks the outbox through the packaged browser UI. Drafting is stubbed by the harness. Each complete run needs a fresh empty schema.
