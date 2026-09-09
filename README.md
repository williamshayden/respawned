# Respawned

Respawned turns opportunity and activity data into ranked follow-ups and an unsent outbox. Integrations submit a small canonical contract over HTTP; deterministic policy handles eligibility, cooldowns, ranking, and validation. An LLM writes copy after candidate selection. Human review is the default, with an explicit operator policy for automatic authorization.

Respawned is the product, Python package, and CLI name. The GitHub repository URL remains unchanged until a separate repository rename.

This repository ships a shared browser review UI, a local PostgreSQL stack, ingestion, reply-inbox, processing, and outbox APIs, a CLI review workflow, configurable policy, Codex and OpenAI-compatible drafting backends, and a legacy demo adapter for the included quote fixtures. The same UI adapts to job applications, sales, and other record kinds. It does not ship a source connector or a message delivery worker.

```mermaid
flowchart LR
    CRM[CRM / product / warehouse] --> Adapter[Thin source adapter]
    Adapter --> API[POST /v1/ingest]
    Demo[Bundled demo adapter] --> Store[(PostgreSQL)]
    API --> Store
    Store --> Reduce[Reduce current state]
    Reduce --> Policy[Eligibility + scoring]
    Policy --> Candidates[One candidate per contact]
    Candidates --> Draft[Codex or API draft]
    Draft --> Review[Human review / explicit automatic policy]
    Review --> Outbox[(Idempotent outbox)]
    Outbox -. future delivery worker .-> Channels[Email / SMS provider]
```

V1 is scoped to one trusted local operator. The source checkout, Python wheel,
source distribution, and application Docker image include the built browser UI.
Running or installing Respawned requires no Node.js runtime or frontend build.
The [release record](docs/V1_RELEASE.md) describes earlier Python V1 artifacts and
checks; it is historical evidence, not evidence that the current Respawned
artifacts have been published or deployed.

## Renaming an existing installation

The Python package and CLI are now `respawned`. Reinstall the project with
`uv sync --frozen`, update scripts and imports to `respawned`, and update any
project-specific `FUE_` environment variables to the `RESPAWNED_` prefix.
Existing database names, users, credentials, and Compose volumes remain valid;
keep their configured values. The new database names in `.env.example` apply
only to fresh installations. Candidate identities and stored records are preserved.
If you rename the checkout directory, keep its existing Compose project name
with `docker compose -p <existing-project>` to continue using the same volumes.
The historical agent transcript and release artifact names retain their original spelling.

## Open Respawned

Configure the database environment using the [quickstart](#quickstart), then
start the bundled application from the source checkout:

```bash
uv sync --frozen
uv run --env-file .env respawned ui
```

The CLI opens [Respawned](http://127.0.0.1:8000) with review access connected;
no manual token is needed. An installed package uses `respawned ui` with the
database environment set. Use `--no-open` for a one-use launch link or `--port`
when 8000 is occupied. The local session survives reloads for 12 hours or until
logout/server shutdown. Setup provides model, record import, and outbox guidance
without seeded records. PostgreSQL stores settings, workspaces, and records.
`respawned serve` and Docker retain optional `RESPAWNED_REVIEW_TOKEN` Bearer access
for scripts and server use; see the [access guide](docs/WEB_UI.md#server-and-api-access).

Use **Workspaces** to create named views for any record kinds, or include all
types. Workspaces persist in the engine and share its records, model, and policy.
The shared queue, context panel, activity history, editable drafts, and unsent
outbox work across these views; contactless records remain visible in All tracked.
See the [UI setup guide](docs/WEB_UI.md) for the complete setup flow, explicit
test simulation, frontend development, and browser checks. Source freshness
remains unknown; approval reserves an unsent outbox item.

## Quickstart

### Prerequisites

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Docker with Compose v2
- `curl` for the API example
- A Codex ChatGPT login or upstream model API credential for live drafting

The project was developed and tested on Windows through WSL2 and Docker Desktop. uv and Docker keep the workflow portable, but native Windows, macOS, other Linux distributions, and production platforms may expose differences in networking, filesystem permissions, bind mounts, and service lifecycle.

### 1. Install and configure

From the repository root:

```bash
cp .env.example .env
uv sync --frozen
```

In PowerShell, use `Copy-Item .env.example .env`.

The template contains host-facing development addresses: `DB_HOST=localhost` and `LITELLM_PROXY_URL=http://localhost:4000`. Compose overrides the database host inside containers, so the same `.env` works for both the host CLI and the containerized API. Published development ports bind to `127.0.0.1` by default; do not expose the unauthenticated API or predictable example credentials to a network.

Use `uv run --env-file .env respawned` from the project root to load the local configuration. The examples below include this full command.

### 2. Start the database and API

For an existing installation created with the old PostgreSQL parent-directory
mount, follow the [storage migration procedure](docs/RELEASE_CHECKS.md) before
recreating containers. Identify and back up the actual data volume first.

```bash
docker compose --profile app up -d --build --wait
curl --fail http://localhost:8000/readyz
```

A ready service returns `{"status":"ready"}`. Readiness checks the database, schema, and policy; `/healthz` separately reports process liveness. The first database-backed request initializes the canonical schema without loading fixture data or requiring a model connection. Serving the initial Setup screen does not need a database connection.
Open [Respawned](http://127.0.0.1:8000) and use **Setup → Review access**
to unlock the engine with the configured `RESPAWNED_REVIEW_TOKEN`. The Docker image includes
the same bundled UI; no separate frontend server or asset mount is needed.
Interactive OpenAPI documentation is available at `http://localhost:8000/docs`.

### 3. Add data

Use **Setup → Records & sources** to upload or paste canonical JSON, inspect its
record and activity counts, then import it. This authenticated UI path accepts
up to 1,000 combined records and activities and a 2 MB JSON payload. Importing
does not draft or send messages. Workspaces organize these records by `kind`;
creating a workspace does not copy or generate records.

For a source integration, translate its records to the canonical API:

```bash
curl -X POST http://localhost:8000/v1/ingest \
  -H 'content-type: application/json' \
  -d '{
    "opportunities": [{
      "id": "opp-123",
      "contact_key": "crm:contact-456",
      "contact_name": "Alex Morgan",
      "contact_email": "alex@example.com",
      "owner_name": "Jordan Lee",
      "value": 12000,
      "status": "open",
      "created_at": "2026-08-20T14:00:00Z",
      "preferred_channel": "email"
    }],
    "activities": [{
      "id": "activity-789",
      "type": "contact_replied",
      "opportunity_id": "opp-123",
      "occurred_at": "2026-08-26T15:30:00Z",
      "channel": "email",
      "direction": "inbound"
    }]
  }'
```

The optional `uv run --env-file .env respawned demo` command explicitly loads
bundled synthetic quote fixtures for testing. It is not run on application
startup. Replaying identical activity payloads is safe; changed payloads with an
existing activity ID are rejected.

For a contactable record, `contact_key` must be a stable, source-provided identity
and at least one of `contact_email` or `contact_phone` must be present. Identity is
separate from a destination: destinations can change, and people may share one.
For a tracking-only record, omit both the key and routes, plus
`preferred_channel`. Partial key/route combinations are rejected. Never use a
no-reply receipt address as an invented recipient.

`kind` defaults to `generic`; `job_application`, `sales`, and other lowercase
identifiers (up to 64 characters, with digits and underscores) use the same UI.
Optional `title` is limited to
300 characters. Optional `context` accepts only `company` (200 characters), `role`
(200), `stage` (100), `summary` (2,000), an aware `expected_reply_at` timestamp, and
an HTTP(S) `source_url` (2,048 characters, no embedded credentials). These are
record facts, not source-controlled policy or executable UI.

Supported opportunity statuses are `open`, `won`, and `lost`; supported delivery
channels are `email` and `sms`. Optional `value` must be nonnegative, with at most
ten integer digits and two fractional digits; values that require rounding are
rejected. Job applications do not use value-based ranking. HTTP and in-process
adapters share the validated contract in `src/respawned/core/contracts.py`.

The built-in policy understands these activity types:

| Activity type | Meaning |
| --- | --- |
| `opportunity_created` | Optional event-stream origin; otherwise `opportunities.created_at` is used |
| `content_viewed` | The contact viewed relevant content |
| `contact_replied` | The contact sent an inbound reply |
| `message_sent` | An outbound message was sent; set `direction` to `outbound` |
| `opportunity_won` | The opportunity reached a won terminal state |
| `opportunity_lost` | The opportunity reached a lost terminal state |

Activities also accept an optional `summary` (2,000 characters), an HTTP(S)
`source_url` (2,048 characters), and `classification`: `human`, `automated`, or
`unknown` (the default). Explicitly human inbound events can use a source-specific
type. Legacy unclassified `contact_replied` events retain their reply semantics;
an explicitly automated event never counts as a human response. The reply inbox
also requires `direction: inbound`. Other event types remain in history without
creating a human reply signal unless explicitly classified as human inbound.

### 4. Preview and persist candidates

```bash
uv run --env-file .env respawned sync --dry-run --now 2026-08-20T12:00:00Z
uv run --env-file .env respawned sync --now 2026-08-20T12:00:00Z
```

The demo uses fixed historical dates, so use the same `--now` for its sync and
review steps. For current source data, omit `--now` to evaluate the current time.

The dry run reports what would be added without writing a sync run or candidate rows. The real run persists up to ten candidates by default, after excluding reviewed candidates and contacts with an active outbox cooldown. Re-running after reviewing the first ten exposes the next eligible contacts. Pending drafts remain available; rejection suppresses that candidate until its identity changes with new evidence, reason, route, or referenced opportunities. Re-running over unchanged state does not create new candidates. Use `--limit`, `--policy`, or a timezone-aware `--now` value to control evaluation:

```bash
uv run --env-file .env respawned sync --dry-run --now 2026-08-20T12:00:00Z --limit 10
```

`--now` limits activity visibility and controls policy evaluation. Opportunities are mutable current snapshots: this option cannot reconstruct earlier statuses, recipients, or values. Outbox reservations later than the evaluation time conservatively block contact, so backdating cannot bypass an existing reservation.

### 5. Optional: start LiteLLM and review drafts

Set a real upstream model and key in `.env`, choose an appropriate `drafting.sign_off` in the policy, then start the proxy:

```bash
docker compose --profile litellm up -d --wait
uv run --env-file .env respawned review --now 2026-08-20T12:00:00Z
```

Candidates appear in score order. Drafts are generated lazily, so opening the queue does not spend tokens for every candidate. Each draft supports:

- `[A]pprove` — recheck terminal state and the contact-wide cooldown, then reserve one outbox row
- `[R]eject` — reject the draft
- `[E]dit` — replace the copy and rerun deterministic validation
- `[S]kip` — keep the draft pending for later; Enter selects this safe default

Approval is idempotent. A database lock serializes approval for one `contact_key`, and a recent source contact or outbox reservation consumes the cooldown. The chosen channel follows `preferred_channel` when that destination exists, falls back to the other available destination, and defaults to SMS when both are present but no preference was supplied.

Approve, reject, and edit actions are bound to the copy and recipient shown to the reviewer. If another reviewer changes the draft, the stale action is blocked; reopen review to inspect the current version. In-process callers must pass `expected_review_token=draft.review_token` to these services. This fingerprint detects changed content. Browser review additionally requires a local `respawned ui` session or the separate `RESPAWNED_REVIEW_TOKEN` bearer credential; it does not use the processing token as human approval authority.

Human review can be toggled in a selected policy:

```yaml
review:
  mode: human  # or automatic
```

`respawned review --policy path/to/policy.yaml` honors this setting. Automatic mode
skips the approval prompt and reserves eligible, validated drafts with
`authorization_mode: automatic`. It does not record human review or send a
message. Existing pending drafts can be authorized after opting in; switching
back to human affects later decisions and does not revoke existing reservations.
See [review modes and HTTP simulation](docs/CONNECTOR_SIMULATION.md) for the API,
configuration, migration, and tested failure cases.

### 6. Inspect or export the outbox

```bash
uv run --env-file .env respawned outbox --path exports/outbox.csv
curl http://localhost:8000/v1/outbox
curl http://localhost:8000/v1/drafts
```

The JSON API and CSV expose reservations and authorization provenance. The API supports bounded `limit`/`offset` snapshot reads; it is not a worker claim or synchronization cursor. CSV timestamps use UTC. Reading or exporting does not send a message or change a reservation's status.

### 7. Stop services

```bash
docker compose --profile app --profile litellm down
```

Named volumes are retained. This V1 uses a canonical schema and has no automatic migration from the earlier quote-specific prototype. Back up prototype data and use a separate database or Compose project for V1. Do not add `--volumes` during normal shutdown.

## Connect another data source

Use `respawned inbox` to see human replies that have no later ingested outbound to the
same contact, including replies received during outreach cooldown:

```bash
uv run --env-file .env respawned inbox
uv run --env-file .env respawned inbox --json --limit 50
curl 'http://localhost:8000/v1/inbox?limit=50'
```

Each item includes the opportunity and activity IDs, recipient route, reply time,
and pending outbox count. Pending reservations do not count as sent answers.
The response explicitly reports unknown source freshness and does not evaluate
outreach eligibility. `--now` (or the API `now` query) needs a UTC offset and only
limits activity visibility; it cannot reconstruct old mutable snapshots.
The inbox is read-only and applies the configured closed/expired exclusions.
Both interfaces return at most 200 contact routes, with `total` and `has_more`;
pagination is not implemented. Source connectors should set `classification:
human` and `direction: inbound` for human replies. Legacy `contact_replied`
events remain supported; explicit `automated` classification always excludes
the event from the human inbox.

A connector should do three things: read from the source using its supported API or webhook, map records to canonical opportunities and activities, and POST batches to `/v1/ingest`. Source-specific pagination, credentials, cursors, rate limits, and field names remain in that thin adapter. The engine never reaches into a customer's CRM database and does not require JSON files.

Opportunities are mutable snapshots and therefore upsert by `id`. Each opportunity object is a full replacement snapshot, not a partial patch: include every optional field that should remain stored, because an omitted optional field is cleared. Activities are immutable facts: an exact replay is idempotent, while the same activity ID with different content returns HTTP 409. This makes webhook retries safe and surfaces broken source identity instead of silently rewriting history.

Opportunity snapshots have no source revision check: an older snapshot can overwrite newer fields. Connectors must currently serialize snapshot updates and avoid replaying old snapshots after newer ones. Immutable activity replay does not provide snapshot ordering.

An activity that arrives before its referenced opportunity also returns HTTP 409. Connectors should deliver the opportunity first and retry the activity batch; no partial batch is committed on either kind of conflict.

HTTP success is emitted after the ingestion transaction commits. If a transport
failure still leaves the outcome unknown, replay the same source identities and
payloads; do not invent new activity IDs to retry.

The current HTTP service is intended for loopback development or a trusted private network. Legacy ingestion and read APIs remain unauthenticated. Protected browser endpoints under `/v1/ui` require a local `respawned ui` session or a configured `RESPAWNED_REVIEW_TOKEN` Bearer credential. Processing is independently disabled until `RESPAWNED_PROCESS_TOKEN` is configured; that credential grants policy-controlled processing, not human approval authority. A public deployment still needs authentication for all routes, tenant scoping, request limits, audit logging, and connector-specific secret management. If you deliberately change `BIND_HOST`, replace every example credential first.

## Install a release artifact

With Python 3.12 or later, the wheel is usable outside a Git checkout:

```bash
python -m venv .venv
# Activate .venv for your shell, then install the downloaded artifact:
python -m pip install /path/to/respawned-1.0.0-py3-none-any.whl
respawned --version
respawned --help
respawned ui
```

Open [Respawned](http://127.0.0.1:8000) to configure the environment in Setup. Both wheel and
source distribution contain prebuilt assets in `respawned/web_assets`; installing
either artifact does not invoke npm or require Node.js. PostgreSQL is needed
for saved settings, workspaces, and engine records.

Set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` in the process
environment before `respawned init`. The CLI does not automatically load
`.env`; `uv run --env-file` in the checkout example does that explicitly. The
installed `demo` includes its fixtures, and `sync`, `inbox`, `review`, and `outbox`
use the packaged policy and schema. A custom policy can be selected with `--policy`;
HTTP uses `RESPAWNED_POLICY_PATH`. Docker Compose configuration, development tests,
and simulation scripts belong to the source checkout, not the wheel. The server
serves the bundled UI by default. Set `RESPAWNED_UI_DIST` to an absolute directory
containing a built `index.html` to override it, or to `off` for an API-only server.

## Configure models and providers

For headless Codex with your existing ChatGPT login, choose **Codex CLI** in
Setup after installing Codex and running `codex login` on the server. No model
API key is needed. The packaged runner is shared with the agent simulations;
the API/CLI core still owns validation and approval. The optional server variables
are `RESPAWNED_CODEX_BIN`, `RESPAWNED_CODEX_SCRATCH_DIR`, and (without saved
settings) `RESPAWNED_MODEL_BACKEND=codex_cli`, `RESPAWNED_CODEX_MODEL`, and
`RESPAWNED_CODEX_TIMEOUT_SECONDS`. See the [backend guide](docs/WEB_UI.md#connect-a-model-backend).

Use **Setup → Model backend** to save an OpenAI-compatible chat-completions API
base URL, model name or alias, timeout, and server credential variable. Settings
persist in PostgreSQL and are shared by browser, API processing, and CLI review.
The API can be a local model server, a direct compatible provider, or LiteLLM.
Keys stay on the server: select `RESPAWNED_MODEL_API_KEY` for a direct backend or
`LITELLM_MASTER_KEY` for the proxy, and set its value in the server environment.
Use the API root reachable from that server, including `/v1` when required.
Saving settings does not make a provider call or verify connectivity; generation
occurs only when requested. Existing drafts and review do not require a model.

Without saved settings, the engine uses `LITELLM_PROXY_URL`,
`LITELLM_MODEL_ALIAS` (default `respawned-default`), `LITELLM_MASTER_KEY`, and
`LITELLM_TIMEOUT_SECONDS`. For the included LiteLLM Compose service, configure its
upstream in `.env`:

```dotenv
LITELLM_UPSTREAM_MODEL=openai/your-model-name
LITELLM_UPSTREAM_API_KEY=replace-with-your-openai-key
```

Provider-qualified LiteLLM model names also support configurations such as `anthropic/...` or `openrouter/...`. Restart the proxy after changing its environment:

```bash
docker compose --profile litellm up -d --force-recreate litellm
```

Model network operations default to a 60-second timeout, configurable with
`LITELLM_TIMEOUT_SECONDS` (greater than zero, at most 300 seconds). SDK retries
are disabled so a failed generation is returned to the workflow for an explicit
retry. This bounds individual network operations, not a whole processing batch.

To expose multiple proxy models, add another entry to `src/respawned/llm/litellm_proxy_config.yaml`, give it a distinct `model_name`, and pass its provider settings into the LiteLLM container. Choose its alias in Setup, or use `LITELLM_MODEL_ALIAS` when no settings are saved. Policy, prompting, and review code remain provider-independent.

## Policy and extensibility

The default policy lives in `src/respawned/config/policy.yaml`:

```text
reason score = base score + global signal weight × normalized signal strength
candidate score = primary reason score + global value weight × value percentile
```

These defaults are transparent placeholders, not fitted coefficients. `base_score` is the one reason-priority mechanism. Signal and value weights are global, avoiding a matrix of reason-specific amount and recency knobs that would be difficult to explain or train consistently. A reason evaluator maps behavior to a zero-to-one signal strength; the highest reason score supplies both the explanation and the drafting tone. Value affects final ordering but cannot change that explanation.

The bundled reasons cover unanswered replies, repeated view days, a viewed-without-reply delay, relatively high-value quiet opportunities, aging opportunities, applications without a human update, and missed expected replies. Job applications are excluded from monetary ranking and the high-value reason. The generic `awaiting_reply` evaluator is available as an opt-in policy entry; see [UI policy examples](docs/WEB_UI.md#follow-up-rules). A new reason using an existing evaluator is only a YAML entry containing an evaluator name, base score, tone, and evaluator parameters. A new kind of signal is one evaluator factory plus a registry entry; the scoring, candidate, drafting, review, and outbox layers remain unchanged. Embedding applications may also inject evaluator factories directly.

The policy uses configurable business-calendar dates through an IANA timezone, a contact-wide cooldown, and a hard dead-after window. It takes the newest outbound time across every opportunity sharing a `contact_key`, including both pre-stream `last_contact_at` and outbound `message_sent` activities. Repeat views count business-calendar dates since the latest outbound contact, not since an inbound reply.

With historical outcomes, the same boundary can support a trained policy: retain hard suppressors as rules, learn a calibrated ranking score from outcome data, version the model alongside its features, and keep a human-readable primary reason for review. The current deterministic implementation is intentionally the auditable baseline for that future comparison.

## Safety and idempotency

Three invariants are enforced at more than one boundary because they are business safety rules, not merely ranking preferences:

- `won`, `lost`, unknown, expired, or unreachable opportunities never become candidates
- one stable contact cannot be approved twice inside the configured cooldown
- retries cannot create duplicate activities, candidates, drafts, or outbox reservations

Before approval, the engine locks the contact's opportunity rows and stable identity, reloads every referenced opportunity, rechecks status and age, checks recent source contact across all sibling opportunities, and checks recent outbox reservations. Ingestion uses the same lock order, so a concurrent terminal-state, route, or outbound-activity update cannot slip past that recheck. Database uniqueness then makes approving the same draft twice a no-op. Generated and edited copy must be non-empty, under the configured character limit, contain no unresolved placeholders or currency amounts, and refer only to open opportunities.

Some checks appear in both ranking and authorization. Ranking filters the queue; authorization rechecks current ingested state before reservation. A future delivery worker must recheck at the point of sending. Source freshness remains unknown.

## Multi-tenant use

For a parent company with 50 teams or business units, identity and policy need an explicit tenant boundary. A production schema would add a tenant key to opportunities, activities, contacts, candidates, drafts, and outbox reservations; uniqueness, cooldown locks, and API authorization would all be scoped by that tenant. Shared corporate rules could provide mandatory suppressors while each unit supplies its own weights, copy guidance, sender identities, channels, and business timezone.

The review surface would also need tenant-aware RBAC, queues, audit history, and routing to the correct delivery credentials. Configuration would likely move from a single YAML file to versioned database records or a configuration service. That creates enough outcome data to evaluate policy and message quality across units without forcing every team into one definition of "follow up."

## What to build next

The [current integration review](docs/INTEGRATION_REVIEW.md) records the shared-core,
local browser session, headless Codex, and outbox checks for this implementation.

The [UI guide](docs/WEB_UI.md) describes the current browser implementation.
The [quality review](docs/QUALITY_REVIEW.md), [development handoff](docs/HANDOFF.md),
and [proposals](docs/PROPOSALS.md) preserve earlier assessments and product
discussion; they do not establish the current release or deployment state.

1. Persist unresolved source associations and add a correction workflow.
2. Improve evidence coverage using real connector results; tracking without a known recipient and bounded review context are already available.
3. Prove one provider adapter with source freshness, snapshot ordering, native drafts, and reconciliation after uncertain outcomes.
4. Add outbox claims, cancellation, and delivery-result recording when implementing a delivery worker.
5. Extend authentication and tenant scoping before shared or public use.

Broader connector frameworks, incremental projections, and learned ranking should follow demonstrated need and measurements.

## Tests

For a repeatable walkthrough with synthetic customers, source retries, changing
recipients, and model failures, see [pre-V1 use-case simulations](docs/SIMULATIONS.md).
The runner saves review transcripts and stored-state evidence without model calls
or sending, and distinguishes demonstrated workflows from product limitations.

The [agent simulation and product assessment](docs/AGENT_SIMULATIONS.md) adds real
headless Codex tool loops, an independent result oracle, and recorded-decision
replay. It requires deliberate model usage when run live; ordinary tests remain
stubbed.

```bash
uv run pytest -q --capture=no
```

With an existing PostgreSQL server, database tests can run without Docker:

```bash
uv run pytest -q --capture=no --ignore=tests/test_app_compose.py \
  --postgres-url 'postgresql+psycopg2://user:password@localhost/test_database'
```

This creates and removes a unique test schema; the role needs permission to create schemas. Existing schemas and data are preserved. Compose lifecycle tests still require Docker and remain part of the default full suite.

The suite covers policy and evaluator behavior, generic reduction, API validation and replay semantics, contact-level grouping, sync idempotency, drafting validation, review-time race guards, outbox export, and isolated PostgreSQL/Compose startup. Model calls are stubbed unless you deliberately run the live review flow.

Frontend checks run separately with `npm run bundle:check`, `npm test`, and
`npm run test:e2e` inside `web`; see [browser checks](docs/WEB_UI.md#checks).
CI pins Node.js 22.20.0 and installs from `web/package-lock.json`. The optional
connected PostgreSQL browser test requires the explicitly started simulation.

## License

Licensed under the [MIT License](LICENSE).
