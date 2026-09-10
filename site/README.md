# Documentation site

This directory builds static documentation and verified downloads for
`https://respawned.williamshayden.com/`. It does not start or expose an application
API, database, model endpoint, or review service.

## Build

Use Python 3.12+ and an isolated environment. The Markdown parser is documentation
tooling only; it is not added to Respawned's runtime dependencies.

```bash
python3 -m venv /tmp/respawned-site-build
/tmp/respawned-site-build/bin/pip install -r site/requirements.txt
/tmp/respawned-site-build/bin/python site/build.py \
  --distribution /absolute/path/to/qualified-distribution \
  --output /absolute/path/to/new-static-output
```

The output path must not exist. Publication builds require a clean checkout and
verify that each allowlisted source file matches the committed revision. For
local development only, add `--allow-dirty-preview`; its manifest is marked
unqualified for publication. The distribution defaults to the qualified 1.0.0
build from `8d0512d4ff0d367b04ec028198195dc18f697c2a`, with its successful installer
verification recorded. The builder checks file sizes, manifest hashes, the exact
`SHA256SUMS` allowlist, and the installer's pinned wheel digest before copying.
It verifies the repository demo and poster against their recorded provenance.
Raw HTML in Markdown is escaped; Markdown tables and fenced code are supported.

CI can qualify a freshly built distribution from its own commit by adding
`--package-source "$(git rev-parse HEAD)"`. This explicit check does not change
the default publication snapshot. Both paths require the recorded installer
verification and exact hashes. Never infer a package revision from an unverified
directory name.

Only the installer, wheel, source archive, checksums, and a public package manifest
are copied from the distribution. Logs, verification reports, credentials,
internal handoff notes, and older distribution directories are excluded.
Static assets also use an explicit allowlist; ignored files and symlinks cannot
enter the output. The public `release.json` removes only two build-time hosting booleans:
`published` and `validation.public_hosting_verified`. Artifact bytes, source
revision, hashes, sizes, and qualification evidence are unchanged. The source
distribution is never modified. `site-manifest.json` records both manifest hashes,
the docs revision, source file hashes, and every emitted file's identity.

## Preview and verify

```bash
python3 -m http.server 8140 --bind 127.0.0.1 \
  --directory /absolute/path/to/new-static-output
```

Check `/`, `/agent-integration/`, `/api/`, in-page anchors, mobile **Contents**, the
collapsed demo, and download links. With reduced motion enabled the demo remains
on its poster. Check a missing route returns HTTP 404 after deployment. Python's
preview server does not apply Cloudflare `_headers` or the custom 404 document.

## Deploy

Use the separately reviewed `site/wrangler.jsonc` with Cloudflare Workers Static
Assets. Point its assets directory at the frozen output and use
`not_found_handling: "404-page"`; do not use a single-page application fallback.
The Worker and custom domain must be scoped to Respawned. Do not route sibling
subdomains or the portfolio through this Worker.

Merge the reviewed source and confirm its checks before building final assets.
Build from a clean checkout so `docs_source_clean` is true. Inspect the output
manifest, upload these exact files, then verify live routes, security headers,
404 status, media, and the installer/wheel/archive checksums. Record deployment
identity and live verification outside the immutable package build metadata.
Link activation follows successful live verification.

## Editing policy

- `site/content/index.md` is the canonical website installation and operation
  guide. Keep changes consistent with `README.md`, `docs/WEB_UI.md`, and the API.
- The agent page and API reference are built directly from
  `docs/AGENT_INTEGRATION.md` and `docs/API.md`; never keep second hand-edited
  copies. Repository-relative links are explicitly mapped to website pages and
  anchors, or revision-pinned source files. New relative links fail the build
  until mapped. Both source files are included in `site-manifest.json`.
- Update demo media and `docs/media/demo-provenance.json` together. The builder
  rejects mismatched files; do not patch generated HTML or media after building.
- Keep package provenance pinned unless deliberately rebuilding and qualifying a
  new distribution. A docs-only change does not silently replace package bytes.
- A new package version needs a reviewed update of the artifact allowlist,
  qualified source revision, and installation paths in this directory.
