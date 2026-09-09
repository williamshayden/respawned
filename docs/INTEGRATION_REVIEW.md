# Respawned UI and integration review — September 9, 2026

This review covers the UI branch after configurable workspaces and connections
to multiple engines were implemented.
It supplements the historical V1 and simulation reports; it is not evidence of
publication, deployment to a public host, or delivery through a real mailbox.

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

## Headless Codex

The previous headless runner existed only in source simulations. It now lives in
the Python package as `llm.codex.CodexRunner`; simulations import it, and saved
`codex_cli` model settings resolve a `CodexDraftingAdapter` in the shared core.
The browser never submits an executable path or command. See
[backend setup](WEB_UI.md#connect-a-model-backend) for installation and environment
variables. The invocation uses the CLI's existing ChatGPT login and structured
noninteractive output; see [official Codex documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

Fresh local qualification used Codex CLI 0.153.4 through WSL calling its Windows
executable, with a Windows-mounted scratch directory:

- Ten actual model calls passed the connector simulation in human and automatic
  modes. Real loopback HTTP and PostgreSQL exercised immutable source replay,
  lost acknowledgments, source checkpoints, failed model calls, outbox identity,
  and repeated export to an unsent mock mailbox draft.
- One additional real call used saved Codex settings through the UI draft API
  and shared validation. Its persisted draft remained `pending`; the outbox had
  zero rows. This confirms the packaged application path, beyond the simulation.
- Ordinary tests use stubs and cover missing/wrong login, invalid output,
  unsuccessful turns, prohibited tool events, timeout, and nonzero process exit.
  Saving settings checks runtime/login availability without making an inference call.
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

## Combined validation

The full local suite passed 578 Python tests, including Docker startup,
persistence, and backup/restore. After the final CORS startup fix, 44 focused
checks passed, including two additional CLI startup regressions. The only warning
is the existing Starlette/httpx deprecation. Frontend validation passed 52 data
tests and 27 Chromium browser tests; the separate opt-in connected approval test
was skipped. Actual local session, two-engine, and Codex/API walkthroughs above
supply additional integration evidence.

Typechecking, the locked frontend build, bundled-asset reproducibility, and
`uv lock --check` passed. Both the wheel and the wheel rebuilt from the source
distribution installed outside the checkout and passed HTTP asset/CLI/PostgreSQL
checks without Node.js. The package probe also imports the Codex backend and
checks `respawned ui --help`. Evidence is recorded locally in
`/tmp/respawned-multi-engine-package-20260909/report.json`.
