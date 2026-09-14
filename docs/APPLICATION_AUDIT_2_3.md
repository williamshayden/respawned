# Respawned 2.3 application audit

Audit date: 14 September 2026. Baseline: published 2.2.0. Scope: the complete application, including browser, HTTP API, CLI/SDK, decision rules, persistence, authentication, model execution, packaging, and documentation.

Respawned imports source facts, determines whether a follow-up is appropriate, prepares a draft, and records an authorized message in an **unsent** outbox. A sending tool delivers the message and submits a receipt. The audit preserves those boundaries. It does not add a sending service or make provider-quality claims.

## Confirmed defects and corrections

| Priority | Reproduction and user impact | 2.3 correction | Regression evidence |
| --- | --- | --- | --- |
| P1 | A draft addressed to Avery about a role at Alpha retained a valid review token after the same source record changed to Morgan and a different role at Beta. Approval stored the updated contact name alongside the old copy. | Bind pending review tokens to canonical contact-group facts and return displayed record context from the same snapshot. Store generation and accepted-review context separately; show changed or unknown draft context. A fresh read is required after source changes. Completed review retries retain their accepted token. | `tests/test_review_context.py`, `tests/test_review.py`, `tests/test_ui_api.py`, CLI review tests. |
| P1 | Eligibility or a deadline could change during model generation or while waiting for approval locks, allowing an outdated decision to become a reservation. | Recheck the current time and canonical eligibility after generation and under the reservation lock. Automatic reuse requires matching source provenance. | Source/deadline drift and automatic-processing cases in `tests/test_review_context.py` and `tests/test_review_toggle.py`. |
| P1 | A receiver on another loopback port captured the synthetic local session cookie and replayed a protected read successfully. Cookies are not isolated by port. | Require a separate random proof on every session-authenticated request. The browser stores it in origin-scoped storage; the server stores only its hash. A cookie alone cannot restore or use the session. | `tests/test_local_session.py`, `web/src/data/auth.test.ts`, and the connected-browser gate. |
| P2 | Searching for imported Contact 205 returned no matches when only the first 50 records were loaded; the ready count also stopped at 50. | Apply query filters after global eligibility calculation and before pagination. Return authoritative totals. Older engines without those totals are fully paginated before local filtering. | `tests/test_ui_api.py`, `web/src/data/recordQuery.test.ts`, browser review regressions. |
| P2 | The reply inbox displayed only the first 200 of 206 routes, with no way to request the remainder. Activity also inherited the currently loaded queue subset. | Add inbox pagination and an independent paged Activity view. Partial activity coverage is explicit. | API inbox paging cases, frontend workflow tests, browser regression coverage. |
| P2 | An empty processing request or a queue with an already supplied draft failed with “Drafting model is not configured,” even though neither required generation. | Resolve the model only when a new generated draft is needed. Keep validation and authorization on the shared workflow. | `tests/test_process_api.py`. |
| P2 | The HTTP stack consumed an oversized unauthenticated body before responding. | Reject bodies over 2,000,000 bytes, including streamed requests with no reliable Content-Length. | `tests/test_api_body_limit.py`. |
| P2 | A timed-out Codex wrapper returned an error while its child process remained alive. | Use an owned process group on POSIX and terminate it on completion, timeout, or interruption. | `tests/test_codex_process.py`; structured-output and tool-rejection tests remain in place. |
| P2 | Record reads repeated the complete source-state reduction, increasing work with growing history. | Share one canonical state snapshot between eligibility, displayed context, and review tokens. | Read-projection tests and the bounded benchmark below. |

## Workflow inspection

The initial browser pass used two isolated engines with synthetic records. Import, manual editing, generation, review, rejection, and export were exercised through browser controls. Generation used a deterministic adapter. No external model or delivery service was called.

Observed healthy behavior included locked setup, invalid-token rejection, an empty first-start view, atomic invalid-import rejection, idempotent import replay, source evidence display, placeholder validation, saved-copy review, unsaved-copy retention, stale second-tab rejection, separate approval into an exact unsent outbox item, and contactless tracking without draft approval.

The full automated suite also covers source ingestion and identity conflicts; state reduction and contact grouping; policy, cooldown, deadline and duplicate suppression; transactional review and receipt handling; API/CLI capability boundaries; settings and workspaces; installed-package resources; and Docker restart and backup/restore. The Docker test now asserts that the new source fingerprints survive recreation and restoration; it must pass in release qualification.

The audit added a connected-browser CI gate against the real packaged UI, HTTP server, and PostgreSQL. This complements the existing browser suite that uses controlled HTTP fixtures. It is a required part of release qualification.

## Performance measurement

The benchmark used one local PostgreSQL process, synthetic open records, and 20 activities per record. Each operation was measured twice at 100, 1,000, and 5,000 records. At 5,000 records / 100,000 activities, the median first-page record read fell from approximately 7.1 seconds to 1.82 seconds. The corrected request issued seven SQL statements and performed one complete state reduction.

This is a bounded local measurement, not a production throughput or latency guarantee. Reads still project the complete relevant history; one-record reads at the largest fixture remained approximately 1.80 seconds. Incremental projections or caching are deferred until a workload justifies their invalidation and consistency costs.

## Upgrade and compatibility

- Restarting the upgraded engine applies two additive, nullable draft fingerprint columns. No historical provenance is invented and no drafts, activities, or reservations are deleted.
- Upgrade engine and review clients together. New pending review tokens carry current source context; older clients cannot safely manufacture that context. The 2.3 CLI keeps older engine responses readable but disables interactive review writes when coherent `review_context` is absent.
- Reconnect local browser sessions after upgrading. The launcher exchanges a fresh proof and cookie. Bearer credentials continue to use their existing authentication path.
- A legacy draft with unknown generation context can be inspected and explicitly reviewed by a person. Automatic processing cannot treat unknown or changed context as current.
- Approval still creates an unsent reservation. Receipt idempotency and already accepted review retries remain protected.

## Qualification record and limits

Release qualification requires the complete Python and browser suites, connected-browser checks, Docker persistence/restore, installed wheel/source execution, metadata, installer, and static-site checks. The [release workflow](RELEASING.md) records the qualified source and artifact hashes; its successful run is the publication gate.

Local browser control became unresponsive after the initial workflow audit. A direct request for the record being inspected returned HTTP 200 in 1.224 seconds, and control also failed on fresh-tab navigation. This was not classified as a product defect. Post-fix browser verification requires successful hosted browser gates before release; a new manual visual, full keyboard, and assistive-technology audit remains unqualified.

Native Windows descendant-tree termination and Windows executables launched through WSL are not covered by the POSIX cleanup guarantee. The existing native subprocess timeout behavior remains. Real provider inference quality, account-specific credentials, and external message delivery were not exercised. These limits do not imply successful qualification of those integrations.

The original working project, user data, music, and existing published demo media were preserved. Synthetic audit databases and logs are separate from release artifacts; the package and documentation allowlists exclude them.
