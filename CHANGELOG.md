# Changelog

## 1.0.0 — release candidate

Initial source release of the local Respawned. The engine accepts
contactable opportunities and activities, ranks follow-up work, generates draft
copy, and reserves an unsent outbox message under operator-selected review policy.

### Included

- Source-neutral HTTP ingestion with shared validation, atomic batches, stable
  activity identities, and conflict detection on changed replay payloads.
- Deterministic candidate selection, contact-wide cooldowns, closed/expired
  suppressors, and transparent policy configuration.
- Human review by default; explicit automatic authorization using the same
  copy, version, recipient, state, cooldown, and duplicate checks.
- Reply-needed inbox, protected processing, draft history, and outbox reads;
  CLI review and CSV export with authorization provenance.
- Bundled synthetic demo, installed-package CLI, PostgreSQL schema/policy
  resources, and a local Docker Compose application profile.
- Repeatable synthetic workflows, optional headless Codex simulations, and
  distribution/persistence/recovery checks.

### Corrections before release

- Commit ingestion before acknowledging HTTP success.
- Reject stale actions after copy or recipient changes; preserve approval
  provenance without retroactively labeling old reservations as human-reviewed.
- Keep later reservations inside cooldown checks, and let reviewed candidates
  yield queue capacity to subsequent eligible contacts.
- Validate direct ingestion like HTTP, preserve exact monetary bounds, render
  source strings literally, and export timestamps in UTC.
- Bundle fixtures for installation outside Git; include the license in Docker
  builds; exclude local secrets and generated evidence from build contexts.
- Separate database readiness from process liveness and bound model network
  timeouts without hidden SDK retries.

### Scope and compatibility

This release is for a single trusted local operator. Ingestion and reads are not
authenticated or tenant-isolated. The processing token enables policy-controlled
work; it does not certify human approval.

Outbox entries are reservations. V1 does not include a delivery worker, actual
mailbox connection, scheduler, web review interface, or hosted service. Native
provider drafts, source freshness, contactless application tracking, and useful
correspondence context remain future work.

Existing canonical databases receive an additive authorization-provenance
column; older outbox rows retain `legacy_unknown`. The quote-specific prototype
schema and existing Docker installations using the old parent-directory volume
mount require the separate migration steps in [release checks](docs/RELEASE_CHECKS.md).

Version `1.0.0` is package metadata, not evidence of publication. See
[the release record](docs/V1_RELEASE.md) for qualification evidence and the final
publication steps.
