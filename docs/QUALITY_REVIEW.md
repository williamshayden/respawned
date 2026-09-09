# Quality review

September 9 publication qualification supersedes the earlier verification counts:
[V1 release record](V1_RELEASE.md), **386 passed with Docker included**, installed
wheel/sdist checks, and isolated persistence/restore verification. Earlier sections
below preserve the findings and evidence from their respective increments.

Completed September 7, 2026, against the implementation following `03f7ec8`.

Latest increment: [configurable review and external HTTP simulation](CONNECTOR_SIMULATION.md)
adds operator-selected automatic authorization, workflow APIs, and an additive
outbox provenance column. **359 tests pass** against isolated PostgreSQL; the four
Docker lifecycle tests remain excluded. The migration-free and human-only
statements in the original review below describe its earlier baseline.

September 8 follow-on: a read-only reply inbox, commit-before-response ingestion,
and Docker build/volume corrections are now in the working tree. Three real
Codex agent cases and one live drafting probe ran; the saved decisions passed
corrected grading after a timestamp-representation bug in the harness was fixed.
The complete non-Docker suite now reports **280 passed**, and the updated eight
deterministic scenarios pass. See [agent simulations and product priorities](AGENT_SIMULATIONS.md)
and [release checks](RELEASE_CHECKS.md). Docker lifecycle and backup/restore still
need verification. The original review below records the earlier increment.

## Purpose and assessment

The implemented product helps a person work through a prioritized queue of
contactable opportunities, generate bounded copy, and explicitly review it before
reserving an outbox message. Its strongest properties are immutable activity
identity, atomic HTTP ingestion, deterministic hard suppressors, contact-wide
cooldown, and separation of model-generated copy from application decisions.

The proposed application tracker serves a broader purpose: keeping evidence and
correspondence organized before a human recipient is known. The current contact
requirement and absence of source evidence/listing services do not support that
workflow yet. This remains the next product slice in [PROPOSALS.md](PROPOSALS.md).

The review prioritized confirmed failures in the working product by consequence,
confidence, and the cost of fixing them without changing stored data. Renaming the
core model, introducing MCP, or adding a connector framework would not resolve
those failures. The shared application services remain an appropriate boundary.

## Improvements

| Confirmed problem | Result |
| --- | --- |
| A reviewer could approve or overwrite copy edited after it was displayed. | Approve, edit, and reject require the displayed draft's content fingerprint and compare it under the draft lock. Same-timestamp edits are covered. |
| Reviewed top-ten candidates repeatedly occupied the limit, starving the next contact. | Review history and active outbox cooldown filter the queue before its limit; pending drafts remain available. |
| Future snapshot contact timestamps could hide a recent real outbound event. | Both snapshot contact time and activities respect the reduction cutoff before the latest outbound is selected. |
| A reservation committed after a waiting reviewer's captured time could escape cooldown. | Reservations later than the evaluation time also block; backdating cannot bypass them. |
| Direct adapters bypassed HTTP validation; monetary values could overflow or round in PostgreSQL. | Shared canonical models validate both paths before database access, including exact `NUMERIC(12,2)` bounds. |
| Concurrent batches/syncs could write overlapping IDs in inconsistent orders. | Ingestion and candidate persistence write IDs in a stable order. This reduces the identified deadlock risk; exhaustive concurrency behavior is not claimed. |
| Source strings could be interpreted as Rich markup. | Queue cells and review errors display source text literally. |
| CSV timestamps depended on database timezone; model clients lacked deterministic cleanup. | Exports use UTC and per-request OpenAI clients close on success and failure. |

## Compatibility and data

There are no new runtime dependencies or table migrations. Existing opportunities,
drafts, and outbox reservations remain intact. Run the existing `init` command to
refresh the SQL view on an existing canonical database; normal application startup
also applies the packaged schema. Prototype databases still require a separate
migration plan.

In-process review callers must supply `expected_review_token=draft.review_token`
to approve, edit, or reject. A fingerprint identifies content; it does not establish
human identity or authorize an agent to act. Direct ingestion now rejects records
that violate the same contract HTTP has enforced, including blank identities,
unknown fields, missing routes, and naive timestamps. Monetary input requiring
rounding is rejected before it reaches storage.

## Verification

- Initial baseline: 171 tests passed, with 32 Docker-dependent tests excluded;
  `uv lock --check` passed.
- Final run: **247 tests passed** using Python 3.12 and isolated PostgreSQL 16.15.
  The four Compose-specific tests were excluded because Docker was unavailable.
- A representative test crosses real PostgreSQL HTTP ingestion, replay and atomic
  conflict rejection, candidate sync, rendered CLI review, explicit test approval,
  outbox persistence, and CSV export. Model responses are stubbed.
- Regression coverage includes stale review tokens, queue progression after ten
  reviews, pending/rejected lifecycle, cooldown boundaries, future timestamps,
  literal CLI rendering, monetary precision, and provider-client cleanup.
- The remaining warning is Starlette's upstream TestClient/httpx deprecation.

The README documents `pytest --postgres-url` for repeating database tests without
Docker. Tests create a unique schema, roll back test records, and remove only that
schema. Container startup and live model/provider integration remain unverified.

A one-off warm dry-run benchmark compared the previous sync service with the new
service on the same PostgreSQL data. Candidate IDs and counts matched. SQLAlchemy
statement instrumentation and three timed runs after warmup produced:

| Candidates requested | SQL statements before / after | Median time before / after |
| --- | --- | --- |
| 100 | 102 / 4 | 25.54 / 8.01 ms |
| 1,000 | 1,002 / 4 | 228.68 / 70.06 ms |

These are local microbenchmark results, not deployment capacity estimates. Full
state reduction still scales with ingested history; no projection redesign is
justified by this measurement alone.

## Remaining priorities

1. Build the contactless tracker and source evidence contract, retaining separate
   applications for distinct roles and explicit correction of ambiguous links.
2. Define source freshness and a real recipient/context contract before delivery.
3. Add authentication and tenant boundaries before exposing the API beyond its
   trusted local scope; retain explicit human authorization for remote review.

Mutable snapshots cannot reconstruct earlier statuses, values, or recipients;
`--now` is an activity cutoff and policy clock, not complete historical replay.
Rejecting a candidate suppresses its current identity until relevant evidence or
identity changes; snoozing and reopening are not implemented. Native Gmail drafts,
monitoring, provider delivery, and MCP remain proposals. There is no trained model
or outcome study supporting policy weights; the defaults remain transparent rules.
