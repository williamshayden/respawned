# Historical development handoff — September 5, 2026

This preserves the `d682d8a` baseline and its verification limits. Its feature
list, local paths, and next steps are historical. Use the [README](../README.md),
[API guide](API.md), and [integration review](INTEGRATION_REVIEW.md) for the
merged Respawned application and current setup.

Updated September 5, 2026. Implementation baseline: `d682d8a` on `main`.

The completed refactor is committed locally. Nothing was pushed or published during this work. Package version `1.0.0` identifies the current code; it does not mean a release was published. The project uses the MIT license.

## What exists

The engine accepts canonical opportunities and immutable activities, reduces them to current state, ranks eligible follow-ups, groups them by contact, drafts messages on demand, and requires human review before reserving a mock outbox row.

| Area | Implemented behavior | Starting point |
| --- | --- | --- |
| HTTP API | `GET /healthz`, `POST /v1/ingest`; typed validation and atomic batches | [api/app.py](../src/respawned/api/app.py), [api/models.py](../src/respawned/api/models.py) |
| Ingestion | Full opportunity snapshot upserts; identical activity replay is ignored; conflicting activity IDs and unknown opportunity references return 409 | [core/ingest.py](../src/respawned/core/ingest.py) |
| State | Canonical PostgreSQL schema and deterministic reduction; latest outbound includes pre-stream contact and source message events | [schema.sql](../src/respawned/db/schema.sql), [core/reduce.py](../src/respawned/core/reduce.py) |
| Policy | Extensible evaluator registry, base scores, global value/signal weights, business timezone, cooldown and age suppressors | [policy.yaml](../src/respawned/config/policy.yaml), [core/policy.py](../src/respawned/core/policy.py), [core/reasons.py](../src/respawned/core/reasons.py) |
| Candidates | One candidate per stable contact identity; email/SMS destination; idempotent sync and latest-run membership | [core/candidates.py](../src/respawned/core/candidates.py), [core/sync.py](../src/respawned/core/sync.py) |
| Drafting | Provider-neutral LiteLLM proxy client; limited name/tone/count payload; deterministic copy validation | [core/draft.py](../src/respawned/core/draft.py), [llm/adapter.py](../src/respawned/llm/adapter.py) |
| Human review | Rich ARES flow; current-state and cooldown checks at approval; transactional outbox reservation | [cli/review.py](../src/respawned/cli/review.py), [core/review.py](../src/respawned/core/review.py) |
| Runtime | `init`, `demo`, `serve`, `sync`, `review`, `outbox`; empty startup; explicit legacy fixture adapter | [__main__.py](../src/respawned/__main__.py) |

The HTTP API currently exposes ingestion and health only. Candidate listing, drafting, and approval are application services used by the CLI; they are not yet HTTP or MCP endpoints.

## Contracts to preserve

- An opportunity payload is a full snapshot, not a patch. Omitting optional fields clears their stored values.
- Activities are immutable facts. Reusing an ID with different content is an error, including differences in nullable fields.
- `contact_key` is source identity; email/phone is a destination. Cooldown spans opportunities sharing that identity.
- The API currently requires a contact key and at least one email/phone destination. Monetary value is optional.
- Statuses remain `open`, `won`, and `lost`. The proposed engagement rename has not happened.
- Ranking excludes terminal, expired, unknown, and unreachable records. Approval rechecks referenced records and cooldown against current internal state.
- Ingestion and approval coordinate through PostgreSQL row/contact locks. These checks depend on writers using the application services and transaction boundaries.
- Outbox uniqueness prevents duplicate reservations for one draft. No provider delivery worker exists, so external exactly-once delivery is not implemented or proven.
- Drafting intentionally receives no raw conversation text, values, or view telemetry. Adding useful conversation context requires an explicit contract.

## Verification and environment

On September 5, 164 tests that do not require Docker passed; `uv lock --check` and Git diff checks also passed. Docker was unavailable inside WSL, so the integration suite was not rerun for the commit. The previous full run recorded in this task passed 203 tests, including Compose/PostgreSQL coverage. That earlier result is historical, not a fresh integration result.

The sole test warning was an upstream Starlette TestClient deprecation. WSL pytest output capture has failed in this environment; `--capture=no` worked. Do not add machine-specific paths or environment workarounds to application code.

The repository is in Ubuntu at `/home/haydenw/Projects/follow-up-engine`. The desktop sometimes supplies the invalid Windows path `C:\home\haydenw\Projects\follow-up-engine`. Run commands inside Ubuntu rather than copying the repository onto the Windows filesystem:

```powershell
wsl.exe -d Ubuntu --cd /home/haydenw/Projects/follow-up-engine -- git status --short
wsl.exe -d Ubuntu --cd /home/haydenw/Projects/follow-up-engine -- uv run pytest -q --capture=no
```

Restore Docker Desktop's Ubuntu integration before the full suite. Serialize Docker-backed tests and integration work; earlier WSL/filesystem problems caused a machine crash. Do not prune unrelated Docker images or volumes.

Use the [README setup guide](../README.md#install-and-start) for setup and manual QA. Development ports bind to loopback. The API has no authentication or tenant isolation. The prototype-to-canonical schema change has no migration path; do not delete an existing data volume without first establishing that its contents are disposable.

## Where the discussion stopped

The next product direction is agent-assisted tracking across sources, using job applications as a concrete workflow. The user wants to discover application confirmations from the last three weeks, list the firms and roles applied for, and attach later correspondence.

Gmail access, application discovery, source references, contextual email drafts, ongoing monitoring, a web review UI, a delivery worker, and MCP support remain proposals. No mailbox has been connected and no monitoring job has been scheduled. The latest direction was to preserve the core and HTTP API, with MCP available later as an optional interface.

See [PROPOSALS.md](PROPOSALS.md) for the workflow, unresolved choices, and suggested implementation slices. Start with an evidence-backed application tracker that can hold a record without a known human recipient. Resolve naming and identity semantics before changing the public schema.
