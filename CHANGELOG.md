# Changelog

## 1.0.0 — release candidate

Initial Respawned release candidate. Import source records, track follow-up work,
review drafts, and export unsent reservations through the bundled UI, CLI, or API.
The rename and browser UI pull requests are merged into `main`; package/release
publication is a separate step.

### Included

- Source-neutral HTTP ingestion with shared validation, atomic batches, stable
  activity identities, and conflict detection on changed replay payloads.
- Deterministic candidate selection, contact-wide cooldowns, closed/expired
  suppressors, and transparent policy configuration.
- Human review by default; explicit automatic authorization using the same
  copy, version, recipient, state, cooldown, and duplicate checks.
- Reply-needed inbox, protected processing, draft history, and outbox reads;
  CLI review and CSV export with authorization provenance.
- Bundled browser UI in wheels, source distributions, and Docker; no Node.js
  runtime required. `respawned ui` opens a local authenticated session without
  copying a review token; `serve --api-only` supports headless operation.
- Configurable workspace views, multiple local/remote engine connections,
  read-only workspace monitoring, and destination-bound review credentials.
- Contactless tracking, arbitrary record kinds, bounded context and source
  links, human/automated activity classification, and application follow-up rules.
- Saved Codex CLI or OpenAI-compatible drafting settings shared by all interfaces.
- Installed-package CLI, bundled synthetic fixtures, PostgreSQL schema/policy
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
- Preserve API priority in the browser and explicit human review under automatic
  processing policy. Selected drafting preserves the CLI's published queue.
- Unify browser/API/CLI outbox exports, including spreadsheet formula escaping.
- Isolate remote credentials and stale responses by engine; show failed monitoring
  checks as stale snapshots instead of zero counts.
- Review existing CLI drafts without requiring model credentials. Bound browser
  setup/workspace waits and report uncertain write outcomes without automatic retries.
- Fix narrow-screen Connections clipping and simplify interface copy and spacing.

### Scope and compatibility

This release is for a trusted operator. Legacy ingestion and read routes are not
authenticated or tenant-isolated; use loopback or a trusted network. UI routes use
separate reviewer access. The processing token enables policy-controlled work;
it does not certify human approval. Workspaces share their engine's data and policy.

Outbox entries are reservations. V1 does not include a delivery worker, actual
mailbox connection, source scheduler, or hosted service. Native provider drafts,
source revision ordering, freshness, and reconciliation remain future work.

Existing canonical databases receive additive context, workspace, model-setting,
and sync-scope fields. Older outbox rows retain `legacy_unknown` authorization
provenance. Initialize the schema and publish a fresh candidate sync after upgrading.
The quote-specific prototype
schema and existing Docker installations using the old parent-directory volume
mount require the separate migration steps in [release checks](docs/RELEASE_CHECKS.md).

Version `1.0.0` is package metadata, not evidence of publication. See
[the integration review](docs/INTEGRATION_REVIEW.md) for qualification evidence;
the [earlier release record](docs/V1_RELEASE.md) retains pre-UI artifact hashes.
