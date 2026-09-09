# V1 release record

Prepared September 9, 2026. Package version: **1.0.0**. This record prepares a
local release candidate; it does not announce publication or deployment.

## What V1 promises

A single trusted operator can install the engine, import canonical contactable
opportunities and immutable activities, inspect unanswered replies, rank work,
generate bounded copy, review it or explicitly select automatic authorization,
and inspect/export unsent outbox reservations. The engine owns durable state,
history, cooldowns, and idempotency in PostgreSQL.

The full qualification target is Python 3.12, PostgreSQL 16, and Linux, including
WSL2 with Docker Desktop. A separate PostgreSQL 18 smoke check verifies startup,
ingestion, readiness, and container-replacement persistence with its parent mount. Python metadata permits newer interpreters, but the release
checks here do not establish native Windows/macOS or every later Python version.
The GitHub source archive includes Compose, development checks, and simulations.
The wheel/sdist contain the application, policy, schema, and bundled demo fixtures.

Ingestion and read APIs are unauthenticated. The processing endpoint is disabled
until configured with a separate operator token. Keep the application on loopback
or a trusted private network; this is not a multi-user hosted-service release.

V1 does not send messages, connect a real mailbox, schedule source updates, expose
a web review UI, or track records without contact identities/routes. Current
drafting context is limited. Optional Codex simulations demonstrate supplied
cases, not a model-quality or reliability benchmark. Source snapshots have no
revision protection, and source freshness remains unknown.

## Qualification

The release run records complete test, Docker, installed-distribution, dependency,
and artifact evidence under the ignored local `exports/release/2026-09-09/`
directory. Final counts and artifact hashes are recorded with the release bundle.

| Check | Result |
| --- | --- |
| Complete test suite | **386 passed, 0 skipped**, including six Docker checks; one upstream TestClient deprecation warning |
| Clean Docker application build | Passed with `--no-cache --pull`; API and PostgreSQL start without a model service |
| PostgreSQL 16 recovery | Exact contents of all six application tables, including an automatically authorized unsent outbox row, survived container replacement and custom-format dump/restore |
| PostgreSQL 18 smoke | Six checks passed on 18.6 using `/var/lib/postgresql`; existing user storage was not used |
| Installed distributions | 28 CLI checks per build, outside the checkout; direct and sdist-built wheels have identical SHA256 |
| Package metadata | Wheel and sdist pass `twine check`; final wheel content matches the current application source and README |
| Synthetic workflows | All eight scripted use cases and both external HTTP connector modes passed; no provider calls |
| Dependency advisory audit | 30 locked runtime packages checked; no known vulnerabilities reported, no packages skipped |
| Workflow and source checks | `actionlint` 1.7.12, dependency lock, whitespace, and local document links passed |

The final wheel SHA256 is
`d4fafafbb1e8c17963793b892bce99aec5294feb99530c3ce4b400101e7db737`.
Distribution evidence is under `distribution-final/`; full test output is in
`pytest.log` and `pytest.xml`. Docker build and independent recovery evidence is
also retained at `exports/release-qualification/2026-09-09-docker/`.

This run did not execute hosted GitHub Actions, native Windows/macOS installation,
or a live provider integration. The optional LiteLLM container profile and its
upstream providers were not qualified; the application profile and adapter's
local HTTP timeout behavior were. The package advisory audit does not cover OS
packages inside container images. Prior Codex simulation results remain separate
exploratory evidence, with their original failures retained.


Reproduce from a source checkout using the locked environment:

```bash
uv sync --frozen
uv lock --check
uv run --frozen pytest -q
uv run --frozen python scripts/verify_distribution.py --output dist/qualification
git diff --check
```

The default tests require a running Docker daemon and Compose v2. They create
uniquely named projects, random host ports, and synthetic data. Lifecycle checks
exercise container replacement and backup/restore in their own project; they
do not migrate an existing installation. Their cleanup removes only test-owned
resources. The application profile starts PostgreSQL and the API without a
model service.

For an existing test PostgreSQL server, use `--postgres-url` with pytest while
excluding `tests/test_app_compose.py`; record that as partial verification.
`verify_distribution.py --postgres-url <test-url>` additionally runs installed
CLI initialization, bundled demo import and replay, dry-run sync, inbox, and CSV
export in isolated schemas. Omitting the URL still checks both installed builds,
all command help, version identity, packaged resources, and failure exit codes.

The distribution verifier builds a direct wheel and an sdist, builds another
wheel from that sdist, compares their contents, and installs each outside the
checkout using locked production dependencies. The build backend is pinned so
the two build paths do not silently choose different backend versions. This
does not prove reproducibility across every future toolchain or platform.

CI runs the full suite and distribution checks on Ubuntu 24.04 with pinned
Actions revisions, Python 3.12, and uv 0.12.3. It has read-only repository
permissions and no publish job. Its definition can be validated locally with
`actionlint`; the hosted workflow still needs to run after an authorized push.

## Release assets and publication

The prepared local bundle is `exports/release/2026-09-09/bundle/` and contains:

- `follow_up_engine-1.0.0-py3-none-any.whl`
- `follow_up_engine-1.0.0.tar.gz` (Python source distribution)
- `follow-up-engine-1.0.0-source.tar.gz` (complete Git source archive)
- `SHA256SUMS` and `manifest.json` identifying the exact commit and assets

Use [CHANGELOG.md](../CHANGELOG.md) as the publication notes. The first release
target is the existing GitHub repository with downloadable Python artifacts.
PyPI upload or a container registry release is a separate publication choice;
no registry credentials or automatic publisher were configured.

After publication is explicitly authorized:

1. Inspect the local release commit and evidence. Confirm the working tree is
   clean and the manifest's commit contains the reviewed changes. Do not release
   an older default-branch commit that happens to have the same version string.
2. Push the reviewed branch and obtain a successful hosted CI run. Merge using
   the repository's chosen process. If the source tree changes, repeat artifact
   qualification and regenerate hashes before continuing.
3. Create `v1.0.0` on the exact qualified commit, then publish the GitHub release
   using that tag and the reviewed assets. Verify downloaded asset hashes.
   Do not allow a release tool to invent or point the tag at an unverified head.
4. Mark the release date in the published notes. Preserve the qualification
   logs and earlier failed simulation evidence separately from the assets.

Nothing in preparation pushes a branch, creates a remote release, uploads a
package, or starts a delivery worker. Local synthetic PostgreSQL and Compose
resources used for verification are stopped after checks finish.

## Existing installations

Read [the migration procedure](RELEASE_CHECKS.md) before replacing an old
PostgreSQL container or upgrading the canonical schema. Back up and restore
the actual data volume; the earlier parent-directory mount could leave data in
an anonymous child volume on PostgreSQL 16/17. The parent mount is correct for
PostgreSQL 18. Preserve both the image major and `DB_DATA_MOUNT`; do not downgrade
an existing 18 installation to the example's 16. Do not remove original storage
during the rehearsal.

Canonical schema initialization adds outbox authorization provenance without
rewriting old messages. It does not convert the quote-specific prototype schema.
Human review remains the default; switching modes does not revoke previous
reservations. CSV consumers should recognize the added `authorization_mode`
column. In-process approval/edit/reject callers must provide the current draft's
review token; that fingerprint detects stale content but is not human identity.
