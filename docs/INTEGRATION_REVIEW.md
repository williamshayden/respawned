# Respawned UI and integration review — September 9, 2026

This review covers the Respawned rename and bundled UI, including configurable
workspaces and connections to multiple engines. Pull requests #2 and #3 are merged
into `main` at `1decc372`.
It supplements the historical V1 and simulation reports; it is not evidence of
publication, deployment to a public host, or delivery through a real mailbox.
The release-readiness pass below follows that merged baseline.

## Application boundary

The browser uses authenticated HTTP operations backed by the same services as
the CLI. [WEB_UI.md](WEB_UI.md#shared-application-boundary) maps each operation.
The review identified and fixed four concrete inconsistencies:

- Browser score sorting changed the API's reply-first priority. Default priority
  now retains API order; alternate presentation sorting remains local.
- Browser controls blocked explicit human approval/rejection under an automatic
  processing policy. They now use the API's human-review operation and provenance.
- Selected drafting replaced the CLI's latest queue. Selected materialization now
  has a separate sync scope and preserves published candidate membership.
- Browser, API, and CLI used different outbox exports. Shared reads and a single
  12-field CSV formatter now serve all three. JSON retains exact source strings;
  CSV escapes formula-like cells for spreadsheets.

Regressions cover changed contact snapshots, selected records beyond the queue,
full-sync refresh, default priority, stale reviews, export parity, workspace
filtering, export failures, and persisted approval/outbound lifecycle. Initialize
the additive schema and run `respawned sync` after upgrading an older database;
historical syncs cannot reliably be classified retrospectively.

## Local browser access

`respawned ui` starts a loopback-only server and opens a one-use launch link.
The browser removes the fragment and exchanges it for an HttpOnly, Strict
SameSite cookie. Sessions last 12 hours, survive reloads, and are invalidated by
logout or process shutdown. Exact Host/Origin and a custom mutation header
protect the cookie path. Port-specific cookies let separate local servers coexist.

A real CLI/PostgreSQL/Chromium walkthrough confirmed connection, persisted
workspace creation, reload, rejected mutation without the required header, and
logout. No JavaScript runtime errors were reported. Automated tests cover expiry,
one-use exchange, cross-origin access, revoked cookies, multiple ports, and the
ordinary server's independent Bearer path. Legacy read/ingest routes still assume
a trusted local deployment; this is not an account or tenant system.

## Multiple engines and workspace monitoring

Connections retain only names, API root URLs, and identifiers in browser storage.
Review credentials remain in tab memory and each request targets its owning
engine. Local sessions cannot be used remotely; remote requests omit cookies and
reject redirects. Servers explicitly opt into allowed browser origins through
`RESPAWNED_UI_ORIGINS`; ordinary Bearer authentication remains required.

The overview reads uncapped counts from canonical engine services without
materializing candidates. Watch preferences distinguish both engine and workspace
identity. Per-engine failures preserve clearly stale snapshots; locking, removal,
or credential changes conceal the previous connection's data. Engine switching
is held during pending writes or unsaved draft edits.

A real Chromium walkthrough used two running servers on different loopback
origins and separate PostgreSQL schemas. It connected both engines, displayed
their distinct record counts (2 and 3), selected saved workspaces in each, then
generated and approved a stub-backed draft only on the second engine. The browser
made exactly those two writes to that engine; its outbox held one pending human
reservation and the first engine's outbox stayed empty. Reload retained connection
and watch metadata, cleared manual credentials, and concealed protected data.
A disallowed browser origin failed CORS even with a valid Bearer token. Desktop
and mobile checks found no runtime errors or horizontal overflow.

These were isolated local servers, not a deployed remote host, and no real model
or delivery provider was called. Evidence lives in
`/tmp/respawned-two-engine-smoke-20260909/`; it is not packaged as application data.

## Historical optional Codex experiments

The existing `codex_cli` adapter remains an optional experiment behind the shared
drafting interface. It is not the product definition or a release requirement.
Respawned does not test Codex login, version, or availability during setup;
configuration status does not establish connection or inference. An explicitly
requested draft invokes the selected backend and validates its result. See
[backend configuration](WEB_UI.md#connect-a-model-backend).

The following earlier experiments used Codex CLI 0.153.4 through WSL calling its
Windows executable with a Windows-mounted scratch directory. Their evidence is
retained independently of backend-neutral application qualification:

- Ten actual model calls passed the connector simulation in human and automatic
  modes. Real loopback HTTP and PostgreSQL exercised immutable source replay,
  lost acknowledgments, source checkpoints, failed model calls, outbox identity,
  and repeated export to an unsent mock mailbox draft.
- One additional real call used saved Codex settings through the UI draft API
  and shared validation. Its persisted draft remained `pending`; the outbox had
  zero rows. This confirms the packaged application path, beyond the simulation.
- The earlier implementation also tested login/runtime preflight probes. Those
  probes have been removed; current setup tests check configuration without
  executable or provider calls. Optional adapter unit tests retain controlled
  coverage of execution output and failure handling.
- A bounded WSL-to-Windows timeout probe observed the exact native child before
  timeout and confirmed it exited afterward; no draft was accepted.

These checks used isolated schemas and synthetic source records. They do not
prove Gmail, Outlook, or another live provider integration. No external message
was sent. Raw local evidence is in `/tmp/respawned-codex-connector-v1-20260909/`
and `/tmp/respawned-live-codex-api-result.json`; those paths are machine-local
qualification output and are not packaged or committed as product data.

## Outbox contract and remaining work

Approval reserves the exact reviewed snapshot. A separate-process CLI export was
byte-identical to both API CSV aliases. Repeated exports did not change status,
timestamps, body, recipient, identity, or authorization provenance. Browser tests
check the downloaded server bytes and ensure a failed request produces no file.

Importing an actual outbound event clears the relevant reply and applies
contact-wide cooldown. It does not infer that a particular outbox row was sent:
that needs explicit provider-message correlation. Until a delivery adapter owns
claims, retries, cancellation, and acknowledgment, outbox rows remain reservations
and the product presents delivery as manual/export-only.

The next integration should prove one provider's source cursors, ordered mutable
snapshots, stale-source handling, native draft identity, and recovery from unknown
outcomes. Preserve unresolved associations for correction. Add shared/public
authentication and tenant boundaries only when that deployment is in scope.
Current workspaces are organizational views over the same engine and cooldowns.

## Qualification before the release-readiness pass

The full local suite passed 578 Python tests, including Docker startup,
persistence, and backup/restore. After the final CORS startup fix, 44 focused
checks passed, including two additional CLI startup regressions. The only warning
is the existing Starlette/httpx deprecation. Frontend validation passed 52 data
tests and 27 Chromium browser tests; the separate opt-in connected approval test
was skipped. The local session and two-engine walkthroughs, plus the separate
optional adapter experiment above, were recorded with that run.

Typechecking, the locked frontend build, bundled-asset reproducibility, and
`uv lock --check` passed. Both the wheel and the wheel rebuilt from the source
distribution installed outside the checkout and passed HTTP asset/CLI/PostgreSQL
checks without Node.js. At that time the package probe also imported the optional
CLI adapter; the generic release probe no longer makes that provider-specific check.
Evidence is recorded locally in
`/tmp/respawned-multi-engine-package-20260909/report.json`.

## Release-readiness pass

The release-readiness pass fixed a CLI dependency error: opening an empty queue or reviewing
an existing draft required model configuration unnecessarily. The CLI now resolves
the backend only when generating missing copy. Browser setup and workspace requests
have bounded timeouts, with no automatic write retries and an explicit warning
when a write's outcome is unknown. Mobile Connections controls no longer clip;
review spacing, engine labels, and empty-state copy were simplified. Switching
records resets the review panel's scroll position while preserving unsaved edits.
Setup now reports saved backend configuration without probing provider runtimes
or logins; no provider-specific login is a product or release prerequisite. The README
now separates local launch from Docker setup, and [API.md](API.md) is the canonical
connector and policy reference.

The connected browser walkthrough starts an empty PostgreSQL schema and uses
the packaged UI directly. Every application write and state assertion is made
through browser controls: rejected import without partial records, file import
and replay, saved model settings across reload, workspace create/edit/watch/remove,
draft editing, stale second-tab refusal, approval, CSV download of reviewed copy,
persistent unsent status, and contactless tracking. Desktop and mobile checks
reported no application runtime errors. Reproduce with the
[empty-engine test commands](WEB_UI.md#checks). Evidence is in
`/tmp/respawned-release-pure-ui-backend-neutral/` for the completed final walkthrough.

The first walkthrough attempt exposed a simulation harness database probe that
ignored its isolated connection override; it now checks the scoped real database.
The second attempt failed on an outdated test text locator after the copy change.
Both failed runs remain at `/tmp/respawned-release-pure-ui-1/` and
`/tmp/respawned-release-pure-ui-2/`.

| Check | Result |
| --- | --- |
| Full Python suite | 592 passed with no failures or skips, including Docker, persistence, and recovery checks; one existing Starlette/httpx deprecation warning |
| Frontend | 55 unit tests, typecheck, and 29 ordinary Chromium tests passed |
| Connected browser | One additional opt-in empty-engine walkthrough passed against the packaged app and real PostgreSQL |
| CLI subprocess workflows | Seven scenarios, 45 actual commands, and nine requests to a local model stub passed; includes replay, review without model credentials, provider errors, and concurrent review conflicts |
| Scripted source workflows | Eight use cases with 99 assertions, plus two HTTP connector modes with 38 checks, passed |
| Installed distributions | Direct wheel and sdist-built wheel contents match; both passed CLI, HTTP asset, and PostgreSQL checks outside the checkout with no Node.js runtime |
| Documentation | 102 local links resolved and the canonical JSON example passed the shared ingestion schema |

CLI evidence is in `/tmp/respawned-cli-release-qualification-20260909/`; scripted
use-case evidence is in `/tmp/respawned-release-usecases-final-20260909/`.
Final distribution evidence and artifact hashes are in
`/tmp/respawned-readiness-package-backend-neutral-20260909/report.json`.
These are local verification
artifacts. This pass used synthetic input and local model stubs; it made no live
provider calls and sent no messages. The earlier real Codex checks remain separate
evidence, not new model runs or a provider reliability benchmark.
