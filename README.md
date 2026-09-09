# Respawned

Respawned turns opportunity and activity data into ranked follow-ups and an unsent outbox. Integrations submit a small canonical contract over HTTP; deterministic policy handles eligibility, cooldowns, ranking, and validation. An LLM writes copy after candidate selection. Human review is the default, with an explicit operator policy for automatic authorization.

This repository ships a local PostgreSQL stack, ingestion, reply-inbox, processing, and outbox APIs, a CLI review workflow, a configurable policy, a provider-neutral LiteLLM adapter, and a legacy demo adapter for the included quote fixtures. It does not ship a message delivery worker or claim to be a CRM.

```mermaid
flowchart LR
    CRM[CRM / product / warehouse] --> Adapter[Thin source adapter]
    Adapter --> API[POST /v1/ingest]
    Demo[Bundled demo adapter] --> Store[(PostgreSQL)]
    API --> Store
    Store --> Reduce[Reduce current state]
    Reduce --> Policy[Eligibility + scoring]
    Policy --> Candidates[One candidate per contact]
    Candidates --> Draft[LiteLLM draft]
    Draft --> Review[Human review / explicit automatic policy]
    Review --> Outbox[(Idempotent outbox)]
    Outbox -. future delivery worker .-> Channels[Email / SMS provider]
```

V1 is scoped to one trusted local operator. It can be installed from the source
checkout or the prepared Python wheel; it is not a hosted service. See the
[release record](docs/V1_RELEASE.md) for artifacts, supported scope, and checks.

## Quickstart

### Prerequisites

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Docker with Compose v2
- `curl` for the API example
- An upstream model API key only if you want live drafting

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

A ready service returns `{"status":"ready"}`. Readiness checks the database, schema, and policy; `/healthz` separately reports process liveness. Startup creates an empty canonical schema without loading fixture data or requiring a model connection.
Interactive OpenAPI documentation is available at `http://localhost:8000/docs`.

### 3. Add data

For the fastest local tour, load the bundled synthetic fixtures through the demo adapter:

```bash
uv run --env-file .env respawned demo
```

This is an explicit compatibility path, not the application's internal data model. Repeating it is safe: mutable opportunities are upserted, exact duplicate activity payloads are ignored, and reusing an activity ID with a different payload is rejected.

To integrate a real source, translate its records to the canonical API instead:

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

`contact_key` must be a stable, source-provided identity. It is intentionally separate from an email address or phone number: destinations can change, and two people can share one destination. At least one of `contact_email` or `contact_phone` is required. Supported opportunity statuses are `open`, `won`, and `lost`; supported delivery channels are `email` and `sms`. Optional `value` must be nonnegative, with at most ten integer digits and two fractional digits; values that require rounding are rejected. HTTP and in-process adapters use the same validated record contract in `core/contracts.py`.

The built-in policy understands these activity types:

| Activity type | Meaning |
| --- | --- |
| `opportunity_created` | Optional event-stream origin; otherwise `opportunities.created_at` is used |
| `content_viewed` | The contact viewed relevant content |
| `contact_replied` | The contact sent an inbound reply |
| `message_sent` | An outbound message was sent; set `direction` to `outbound` |
| `opportunity_won` | The opportunity reached a won terminal state |
| `opportunity_lost` | The opportunity reached a lost terminal state |

Other activity types are retained but ignored by the built-in evaluators, which allows integrations to add custom policy evaluators without changing ingestion.

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

Approve, reject, and edit actions are bound to the copy and recipient shown to the reviewer. If another reviewer changes the draft, the stale action is blocked; reopen review to inspect the current version. In-process callers must pass `expected_review_token=draft.review_token` to these services. This fingerprint detects changed content; a future remote approval interface still needs an explicit human authorization mechanism.

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
pagination is not implemented. Source connectors must classify human replies as
`contact_replied` with `direction: inbound` and keep automated replies separate.

A connector should do three things: read from the source using its supported API or webhook, map records to canonical opportunities and activities, and POST batches to `/v1/ingest`. Source-specific pagination, credentials, cursors, rate limits, and field names remain in that thin adapter. The engine never reaches into a customer's CRM database and does not require JSON files.

Opportunities are mutable snapshots and therefore upsert by `id`. Each opportunity object is a full replacement snapshot, not a partial patch: include every optional field that should remain stored, because an omitted optional field is cleared. Activities are immutable facts: an exact replay is idempotent, while the same activity ID with different content returns HTTP 409. This makes webhook retries safe and surfaces broken source identity instead of silently rewriting history.

Opportunity snapshots have no source revision check: an older snapshot can overwrite newer fields. Connectors must currently serialize snapshot updates and avoid replaying old snapshots after newer ones. Immutable activity replay does not provide snapshot ordering.

An activity that arrives before its referenced opportunity also returns HTTP 409. Connectors should deliver the opportunity first and retry the activity batch; no partial batch is committed on either kind of conflict.

HTTP success is emitted after the ingestion transaction commits. If a transport
failure still leaves the outcome unknown, replay the same source identities and
payloads; do not invent new activity IDs to retry.

The current HTTP service is intended for loopback development or a trusted private network. Ingestion and reads are unauthenticated; processing is disabled until an operator configures `RESPAWNED_PROCESS_TOKEN`, then requires that bearer token. That token grants policy-controlled processing, not human approval authority. A public deployment should add authentication, tenant scoping, request limits, audit logging, and connector-specific secret management. If you deliberately change `BIND_HOST`, replace every example credential first.

## Install a release artifact

With Python 3.12 or later and a PostgreSQL 16 database, the wheel is usable outside
a Git checkout:

```bash
python -m venv .venv
# Activate .venv for your shell, then install the downloaded artifact:
python -m pip install /path/to/respawned-1.0.0-py3-none-any.whl
respawned --version
respawned --help
```

Set `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` in the process
environment before `respawned init`. The CLI does not automatically load
`.env`; `uv run --env-file` in the checkout example does that explicitly. The
installed `demo` includes its fixtures, and `sync`, `inbox`, `review`, and `outbox`
use the packaged policy and schema. A custom policy can be selected with `--policy`;
HTTP uses `RESPAWNED_POLICY_PATH`. Docker Compose configuration, development tests, and
simulation scripts belong to the source checkout, not the wheel.

## Configure models and providers

The application talks to an OpenAI-compatible LiteLLM proxy, not directly to Anthropic, OpenAI, OpenRouter, or another provider. The default alias is `respawned-default`. Change providers without changing application code by editing two values in `.env`:

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

To expose multiple models, add another entry to `src/respawned/llm/litellm_proxy_config.yaml`, give it a distinct `model_name`, pass its provider settings into the LiteLLM container, and set `LITELLM_MODEL_ALIAS` to the alias the drafting workflow should use. Policy, prompting, and review code remain provider-independent.

## Policy and extensibility

The default policy lives in `src/respawned/config/policy.yaml`:

```text
reason score = base score + global signal weight × normalized signal strength
candidate score = primary reason score + global value weight × value percentile
```

These defaults are transparent placeholders, not fitted coefficients. `base_score` is the one reason-priority mechanism. Signal and value weights are global, avoiding a matrix of reason-specific amount and recency knobs that would be difficult to explain or train consistently. A reason evaluator maps behavior to a zero-to-one signal strength; the highest reason score supplies both the explanation and the drafting tone. Value affects final ordering but cannot change that explanation.

The bundled reasons cover unanswered replies, repeated view days, a viewed-without-reply delay, relatively high-value quiet opportunities, and aging opportunities. A new reason using an existing evaluator is only a YAML entry containing an evaluator name, base score, tone, and evaluator parameters. A new kind of signal is one evaluator factory plus a registry entry; the scoring, candidate, drafting, review, and outbox layers remain unchanged. Embedding applications may also inject evaluator factories directly.

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

See the [quality review](docs/QUALITY_REVIEW.md) for the latest assessment and verification, the [development handoff](docs/HANDOFF.md) for project history, and [proposals](docs/PROPOSALS.md) for the agent-assisted job-application workflow and open decisions. Those proposals are not implemented features or release commitments.

1. Supply bounded source context so drafts address the actual request, and show that context during review.
2. Persist unresolved associations and permit tracking records before a human recipient is known.
3. Prove one provider adapter with source freshness, snapshot ordering, native drafts, and reconciliation after uncertain outcomes.
4. Add outbox claims, cancellation, and delivery-result recording when implementing a delivery worker.
5. Add authentication, tenant scoping, and a focused review interface before shared or public use.

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

## License

Licensed under the [MIT License](LICENSE).
