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

## Start the demo

From the repository root:

```bash
uv sync --frozen
uv run respawned serve --host 127.0.0.1 --port 8000
```

After installing a wheel or source distribution, run the same `respawned serve`
command without `uv run`.

Open [Respawned](http://127.0.0.1:8000). The default demo uses fictional records
and deterministic draft copy, without a database or model provider. The server
serves the files; demo actions do not call its workflow APIs.
Demo edits and outbox decisions persist in browser storage when available. They
do not create engine records. The UI labels this workspace as Demo.

Use the workspace selector to switch contexts. **Ready for review** shows action
items; **All tracked** also includes waiting, closed, and contactless records.
**Reply inbox**, **Outbox**, **Activity**, and **Policy** use the same navigation.
Opening a contactless record allows inspection, but does not generate a recipient
or enable drafting. **Approve to outbox** records an unsent reservation.

## Connect a local engine

First configure the database and API using the [root quickstart](../README.md#quickstart).
Set `RESPAWNED_REVIEW_TOKEN` in the API process environment or its `.env` file to a
separate, unpredictable reviewer credential. Leave `RESPAWNED_PROCESS_TOKEN` unset unless
you also intend to enable the independent processing endpoint. Compose passes the
reviewer token into the app container when it is started or recreated.

For a host server, from the repository root:

```bash
uv run --env-file .env respawned serve --host 127.0.0.1 --port 8000
```

Use either this host server or the Compose app, then open
[Respawned](http://127.0.0.1:8000). The browser UI and API share that origin.
The Docker image copies the prebuilt assets with the Python source; it needs
neither a separate frontend server nor an asset mount.

Open **Workspace connection settings**, enter the reviewer token, and choose
**Connect local engine**. The token stays in browser memory for this session.
The server checks it before resolving database or provider dependencies for
`/v1/ui` routes. This does not secure the older ingestion/read endpoints or add
tenant isolation; keep the service within its trusted local deployment scope.

Queue refresh reevaluates ingested data and does not contact an external source.
Reading records, editing existing drafts, and inspecting the outbox need no model.
**Generate draft** calls the configured drafting adapter. Reuse the existing
[model and provider configuration](../README.md#configure-models-and-providers)
and choose the policy's sender/sign-off settings before generating real copy.
No provider credential belongs in the browser or a frontend environment variable.

The server rechecks current status, recipient, contact-wide cooldown, and draft
version when saving or approving. A stale review returns a conflict; refresh and
inspect the current copy before acting again. Approval writes to the engine's
outbox with human authorization provenance and does not send a message.

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
using the simulation token. Generate a draft, edit it, save, approve it,
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

Open [the development UI](http://127.0.0.1:5173). Its default workspace is the same
fictional-data demo. To connect it to an API or simulation on a different port,
set the development proxy address when starting Vite:

```bash
RESPAWNED_API_URL=http://127.0.0.1:8001 npm run dev
```

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

`npm run preview` serves the production frontend on port 4173 for a demo preview.
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
Ordinary browser tests use the demo and mocked HTTP; the real PostgreSQL browser
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
