# Respawned browser review UI

Respawned provides one React interface for job applications, sales, and
other follow-up contexts. It shows ranked records, the reason for action, record
facts, source-linked history, editable drafts, and an unsent outbox. The layout and
review controls stay the same as the selected record kind changes.

The built UI ships with the V1 application in the Python wheel, source
distribution, and Docker image. The server serves it from `respawned/web_assets`
by default; installation and runtime do not require Node.js or npm.
[V1_RELEASE.md](V1_RELEASE.md) records earlier Python artifacts and verification;
it is historical evidence, not a publication record for the current Respawned
artifacts. The UI does not connect to a mailbox, refresh an external source, or
deliver messages. Connected records report source freshness as unknown.

## Shared application boundary

The browser is an HTTP client of the same Python application services used by
the CLI. It holds selection, search, display ordering, and unsaved text locally;
records, workspaces, configuration, draft versions, and outbox state come from
the API. Browser length checks provide immediate feedback. The server validates
the persisted message and current record again before accepting edits or review.

| Operation | Browser/API entry point | Shared application service / CLI |
| --- | --- | --- |
| Import source facts | `POST /v1/ui/import`, `POST /v1/ingest` | `core.ingest.ingest_records`; in-process adapters use the same validation |
| Refresh candidates | `POST /v1/ui/sync` | `core.sync.sync_candidates`; `respawned sync` |
| Read queue and evidence | `GET /v1/ui/records`, `GET /v1/ui/inbox` | `core.ui_queries` projects canonical reduction and candidate results; `core.inbox` also powers `respawned inbox` |
| Generate, edit, approve, reject | `/v1/ui/records/{id}/draft`, `/v1/ui/drafts/{id}/…` | `core.review`; `respawned review` |
| Configure model and workspaces | `/v1/ui/setup/model`, `/v1/ui/workspaces` | `core.settings` and `core.workspaces`; saved settings also resolve for CLI and API processing |
| Read and export reservations | `/v1/ui/outbox`, `/v1/ui/outbox/export`, `/v1/outbox/export` | `core.outbox`; `respawned outbox` |

The API owns eligibility, contact grouping, cooldowns, default queue priority,
validation, and authorization provenance. The browser preserves the server's
priority order. Explicit browser approval is human authorization even when the
operator configured automatic processing; it never changes that processing policy.
Approving or rejecting submits the displayed draft version for a transactional
server check, without the browser constructing a reservation.

Generating one selected draft recomputes eligibility against the whole engine
but materializes only that candidate. Its `selection` sync run does not replace
the last published `queue` sync or move an existing candidate out of it, so
switching from browser drafting to CLI review retains the queue. A subsequent
explicit sync publishes a new queue normally. Existing databases receive the
additive `sync_runs.scope` column during initialization; historical runs retain
their queue classification. Run `respawned sync` once to publish a fresh queue
after upgrading from a build that did not distinguish selected drafting.

Workspaces and model configuration have API operations without a dedicated CLI
subcommand. The UI calls those operations directly; it contains no alternative
storage or evaluation engine. Fixtures and the simulated review client are
test utilities and are not imported by the application entry point.

## Start the application

Configure PostgreSQL using the [root quickstart](../README.md#quickstart), then:

```bash
uv sync --frozen
uv run --env-file .env respawned ui
```

After installing a wheel or source distribution, set the database environment
and run `respawned ui` directly. The CLI binds `127.0.0.1:8000` and opens the
bundled UI with an authenticated session. No review token needs to be generated
or copied. Use `--port 8001` for another port, or `--no-open` to print a link
for a browser on this machine. The launcher checks the port before printing it.

The one-use link expires after five minutes. The browser immediately removes
its secret fragment and exchanges it for an HttpOnly, SameSite=Strict cookie.
The session survives reloads for 12 hours, until **Lock access**, or until the
server stops. Restart `respawned ui` for a fresh link after expiry or logout.
This mode is one local operator and one server process; it validates the exact
host and origin and requires a same-origin header for browser mutations.

The interface opens **Setup**. Configure a model, create workspaces, and import
your records there. Normal startup creates no sample records. The initial page
can load without PostgreSQL; durable configuration and records require it.

### Server and API access

`respawned serve --host 127.0.0.1 --port 8000` retains Bearer authentication for
the review API and supports the packaged UI in Docker. Set a separate random
`RESPAWNED_REVIEW_TOKEN` in that server's environment, restart it, then enter
the value under **Setup → Review access**. Manually entered credentials remain
only in tab memory and clear on reload. Scripts send the same value as
`Authorization: Bearer <token>`; it is a shared operator password, not an account
or model credential. A URL-safe value is convenient but no special token format
is required. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Changing the server value revokes that Bearer credential. The local launcher
session is separately revocable and needs no environment token. The public
session-status/bootstrap endpoints reveal only connection availability; protected
review operations authenticate before database or provider dependencies.

The separate `RESPAWNED_PROCESS_TOKEN` enables policy-controlled `/v1/process`;
it does not authorize human review. The draft's internal `review_token` is an
automatic fingerprint of the displayed version and recipient. It is not a
password: the UI submits it to reject stale edits and approvals.

Queue refresh reevaluates imported data without contacting an external source.
Reading records and reviewing existing drafts need no model. Approval rechecks
current state, recipient, cooldown, and version, then reserves an unsent outbox
message with human provenance. Legacy ingestion/read endpoints remain intended
for a trusted local deployment; these access modes do not add tenant isolation.

## Configure workspaces

Open **Workspaces** and choose **New workspace**. Give the view a name, an
optional description, and the record types it should contain. **Include all**
includes every current and future kind. The type list is discovered from the
whole database, including records outside the current queue page. **Add a
record type** lets you prepare a view before that type has been imported.

Use your own lowercase type identifiers, such as `partnership` or `project`;
each allows letters, digits, and underscores up to 64 characters and starts with
a letter. Use that same value as `kind` when importing records. Names allow 120
characters, descriptions 2,000, and a view can select up to 100 unique kinds.
No job, sales, or other domain workspace is predefined.

**Save workspace** persists the view in PostgreSQL so it returns after reconnecting
or restarting. Open a saved workspace to use the shared review layout. Filtering
occurs before pagination and its result count reflects the selected kinds.
**Ready for review** shows action items; **All tracked** also includes waiting,
closed, and contactless records. A record without a confirmed recipient can be
inspected but cannot become a drafting candidate.

Workspaces share one engine's records, contacts, model settings, review policy,
and outbox. They are organizational views, not accounts or isolation boundaries.
A recent outbound for a contact in another view still applies its cooldown.
**Remove workspace** removes only the saved view; its records, drafts, and outbox
remain stored. The authenticated `/v1/ui/workspaces` API supports listing,
creation, updates, and deletion. `/v1/ui/records?workspace_id=<id>` applies a
saved view before pagination; direct record links can still open related records.

## Connect a model backend

Choose a backend under **Setup → Model backend**. Non-secret settings persist in
PostgreSQL and override environment defaults for browser drafting, API processing,
and CLI review. Saving makes no model request. Existing drafts remain available
when a provider is offline. Set your policy's sender/sign-off before real drafting.

### Codex CLI with ChatGPT

Install Codex on the server, run `codex login` with ChatGPT, and select **Codex
CLI · ChatGPT login**. Leave the optional model blank to use the CLI default,
or enter an available model. No API key is required. Setup checks the executable
and login, while draft generation provides the actual inference check.

If Codex is not on PATH, set `RESPAWNED_CODEX_BIN` to its executable and restart
the server. `RESPAWNED_CODEX_SCRATCH_DIR` optionally selects an existing temporary
working directory. Calling Windows Codex from WSL requires that directory to be
on a mounted Windows drive (for example `/mnt/c/...`); these are host settings,
not paths submitted by the browser. Codex must also be installed and logged in
inside an application container if that is where the server runs.

The packaged adapter uses `codex exec` with the existing ChatGPT login,
`--ignore-user-config`, `--ephemeral`, a read-only sandbox, structured JSON output,
and disabled shell, web, apps, plugins, hooks, and memories. It passes an allowlist
of runtime/login environment variables, excluding application secrets and API
billing overrides. Missing login, invalid output, failed turns, tool use, and
timeouts fail the draft. The engine still validates the returned copy; Codex has
no approval or delivery operation. The simulation harness imports this same runner.

Without saved settings, select `RESPAWNED_MODEL_BACKEND=codex_cli`, optionally
`RESPAWNED_CODEX_MODEL`, and `RESPAWNED_CODEX_TIMEOUT_SECONDS` (default 120,
greater than zero and at most 300). Login/version checks have separate bounded
timeouts. See the [current integration review](INTEGRATION_REVIEW.md) for evidence.

### OpenAI-compatible API or LiteLLM

Enter the API base URL, model name or proxy alias, timeout, and server credential
variable. Use the API root reachable from the server, including `/v1` when
required. Choose `LITELLM_MASTER_KEY` or `RESPAWNED_MODEL_API_KEY`, set the key on
the server, and restart it. The browser never accepts or returns the secret.
For an unauthenticated local endpoint, use a local-only placeholder in the selected
variable because the client requires a nonempty value.

Configured status means a credential is present, not that inference was tested.
The default environment backend is `openai_compatible`; existing LiteLLM variables
continue to work. See [provider configuration](../README.md#configure-models-and-providers).

## Import records and use the outbox

Under **Setup → Records & sources**, choose a JSON file or paste a canonical
payload containing `opportunities` and `activities`. The interface previews their
counts; submit **Import records** to validate and save the batch. The import
allows up to 1,000 combined items and 2 MB. Download the template for field names
and replace its values with your own source IDs and timestamps. Import does not
generate drafts or send messages. Re-importing a record ID replaces its complete
snapshot, including clearing omitted optional fields. Activity IDs represent
immutable facts, so exact replays are accepted but changed payloads conflict.

For ongoing integrations, map the source API or webhook to the same canonical
contract. The authenticated browser path is `POST /v1/ui/import`; the existing
`POST /v1/ingest` remains available to trusted local adapters. Source connectors
own their credentials, pagination, and refresh schedule. Importing does not
connect a mailbox or establish source freshness.

Review the record, generate a draft when eligible, edit and save it as needed,
then choose **Approve to outbox**. The **Outbox** shows the resulting unsent
reservation. Download CSV or JSON from the outbox, or export CSV with
`respawned outbox --path outbox.csv`. Browser, API, and CLI use the same 12-field
CSV formatter. CSV prefixes cells that could be interpreted as spreadsheet
formulas; use JSON to preserve the exact original strings. Authenticated clients
can use `GET /v1/outbox/export?format=json|csv`; the UI alias is
`/v1/ui/outbox/export`. An optional `workspace_id` filters full approved snapshots.
Neither export sends a message or marks it sent. Once a message has actually
been sent, ingest its `message_sent` activity with `direction: outbound` and the
real source timestamp. The built-in V1 outbox has no provider connection or
automatic delivery worker; Setup presents that current workflow explicitly.
An outbound event updates reply/cooldown state but does not identify which outbox
row was delivered. Until a provider integration records an explicit correlated
delivery result, that row remains an unsent reservation.

## Run the connected simulation

The simulation exercises the real API and PostgreSQL with fictional records and
stubbed draft generation. It uses no model, mailbox, delivery service, or caller
`.env` file. Supply a loopback PostgreSQL database whose role can create schemas:

```bash
uv run python scripts/serve_ui_simulation.py \
  --postgres-url 'postgresql+psycopg2://user:password@127.0.0.1:5432/test_database' \
  --port 8001
```

The harness creates one uniquely named `respawned_ui_simulation_*` schema. It preserves
other schemas and removes its own on normal shutdown. It fixes the evaluation
clock at September 9, 2026, and includes a job application with a missed expected
reply, a sales reply, and another application with only an automated receipt and
no known human contact. Its deliberately known reviewer token is
`ui-simulation-review-token`; use it only with this simulation.

Open the bundled UI at [http://127.0.0.1:8001](http://127.0.0.1:8001) and connect
through **Setup → Review access** using the simulation token. Create a workspace
for any desired kind or use all records. Generate a draft, edit it, save, approve it,
and inspect the persisted unsent outbox. The contactless application remains
trackable without becoming a drafting candidate. Restart the harness for a fresh
schema when repeating the complete approval walkthrough.

## Record context

Opportunity snapshots accept `kind` (default `generic`), optional `title`, and a
bounded `context` object. Existing source payloads remain valid. For example:

```json
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
    "summary": "Application received; no human contact is known.",
    "source_url": "https://example.com/applications/123"
  }
}
```

This record intentionally has neither `contact_key` nor a contact route. Once a
recipient is confirmed, ingest a complete replacement snapshot with its stable
contact identity and email or phone route. Company/role labels do not merge
records or establish contact identity. Multiple applications at the same company
remain distinct records.

Context accepts only `company`, `role`, `stage`, `summary`, `expected_reply_at`,
and `source_url`. The expected reply must include a timezone offset; links must
use HTTP(S). See the [ingestion contract](../README.md#3-add-data) for bounds.
Omitted snapshot fields are cleared, so resubmit optional facts that should stay.

Activities accept `summary`, `source_url`, and `classification` with values
`human`, `automated`, or `unknown` (default). Mark an automated confirmation as
`automated`. Explicit human inbound events count as responses even with a
source-specific activity type. Legacy `contact_replied` events retain their old
meaning unless explicitly automated; the reply inbox also requires inbound
direction. Immutable activity IDs cannot be reused with changed classification
or content.

Drafting receives only the selected kind, title, company, role, stage, and bounded
summary alongside the existing contact/tone/sender context. Source links, raw
activity feeds, expected timestamps, and monetary values are not added to the
draft payload. Source facts are treated as data, not instructions.

## Follow-up rules

The bundled policy enables `application_no_update` after 7 calendar days in the
configured business timezone without a human update or outbound, and
`promised_update_overdue` after an expected
reply date with a 1-calendar-day grace period. Strength matures over configurable
14-day and 7-day horizons respectively. A newer human reply or outbound at or
after the promised deadline suppresses that old promise. Contact-wide cooldown,
closed/expired exclusions, and approval checks still apply.

Job applications do not participate in monetary ranking or the high-value reason.
Other existing generic rules remain available. The generic `awaiting_reply`
evaluator is implemented but disabled by default to preserve existing policy
behavior. Add an entry under `reasons` in a custom policy to enable it:

```yaml
reasons:
  awaiting_reply:
    evaluator: awaiting_reply
    base_score: 40
    tone: courteous and concise
    params:
      minimum_days: 7
      horizon_days: 14
```

Keep the rest of the policy's required settings and any other desired reasons.
`minimum_days` controls the wait after outbound with no subsequent human reply;
`horizon_days` controls signal-strength growth after that threshold. HTTP reads
the custom policy through `RESPAWNED_POLICY_PATH`; CLI commands accept `--policy`.
The server owns policy; the UI displays it without accepting policy changes.

## Frontend development and bundled assets

Node.js 22.20.0 is used for frontend development and CI. It is not a product
installation or runtime dependency. To work on the UI, start Vite from `web`:

```bash
npm ci
npm run dev
```

Open [the development UI](http://127.0.0.1:5173). It opens the same Setup flow.
To connect it to an API or explicit simulation on a different port,
set the development proxy address when starting Vite:

```bash
RESPAWNED_API_URL=http://127.0.0.1:8001 npm run dev
```

The local session launcher is for the bundled same-origin UI. Use a Bearer
token with a separate Vite development server.

`RESPAWNED_API_URL` defaults to `http://127.0.0.1:8000`. Vite forwards `/v1` and
`/readyz` requests to that API. This variable is a development API address, not a
token, and does not configure the packaged server.

After changing frontend sources, regenerate the package assets from `web`:

```bash
npm run bundle
npm run bundle:check
```

`bundle` builds the UI into `src/respawned/web_assets` with third-party notices
and a source manifest. Keep those generated files with the frontend source
change. `bundle:check` rebuilds and verifies that the checked-in package assets
match the locked sources and dependencies; it fails for missing or stale assets.
Both the wheel and source distribution include these prebuilt files. Building
or installing the Python package does not launch a frontend build.

The default `respawned serve` command serves the bundled assets. To serve a
separate build, first run `npm run build` in `web`, then from the repository root:

```bash
RESPAWNED_UI_DIST="$PWD/web/dist" \
  uv run --env-file .env respawned serve --host 127.0.0.1 --port 8000
```

`RESPAWNED_UI_DIST` accepts an absolute directory containing `index.html`, or
`off` to disable the UI and serve only the API. Unset or empty selects the bundled
UI. Missing or invalid asset directories fail startup clearly. API routes are
registered before the asset mount, so reviewer authentication keeps working.

`npm run preview` serves the production frontend on port 4173 for a static preview.
It does not use Vite's development API proxy; use the API's built-asset serving
for a connected production-build walkthrough.

## Checks

From `web`:

```bash
npm ci
npm run bundle:check
npm test
npx playwright install --with-deps chromium
npm run test:e2e
```

CI verifies bundle freshness and runs frontend checks with Node.js 22.20.0.
Ordinary browser tests use controlled HTTP fixtures; the real PostgreSQL browser
test is skipped unless explicitly enabled. To run it, start a fresh simulation
on port 8001 as above, stop any existing Vite server on 5173, then run:

```bash
RESPAWNED_API_URL=http://127.0.0.1:8001 RESPAWNED_LIVE_UI_TEST=1 \
  npm run test:e2e -- tests/live-ui.spec.ts
```

Playwright starts Vite. That test saves, approves, and verifies one synthetic
draft in PostgreSQL, so it expects a fresh simulation schema. Screenshots/traces
go to temporary output paths, not versioned project files. These checks establish
local behavior; they do not prove mailbox integration, source completeness,
delivery, or a successful hosted deployment.
