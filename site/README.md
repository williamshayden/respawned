# Documentation site

Build static documentation and verified downloads for `https://respawned.williamshayden.com/`. This site serves documentation and files, not the application API.

## Build

Use Python 3.12+ and the documentation dependencies:

```bash
python3 -m venv /tmp/respawned-site-build
/tmp/respawned-site-build/bin/pip install -r site/requirements.txt
```

The 2.1.0 download source must be pinned by `PACKAGE_COMMIT` in `site/build.py` after distribution and installer qualification. The [release workflow](../docs/RELEASING.md) produces the packages once for every enabled channel. Qualify the artifacts before updating the source pin.

For a local preview, set `PACKAGE_SOURCE` to the full qualified application commit and use a new output directory:

```bash
/tmp/respawned-site-build/bin/python site/build.py \
  --distribution /absolute/path/to/qualified-2.1.0-distribution \
  --package-source "$PACKAGE_SOURCE" \
  --output /absolute/path/to/new-preview \
  --allow-dirty-preview
```

Preview manifests are marked unqualified for publication. A publication build requires a clean checkout and verifies that every allowlisted source matches the committed revision. CI can pass its exact `HEAD` as `--package-source` when qualifying artifacts built from that commit.

## Preserve earlier downloads

Published installers pin versioned wheels. Preserve 1.0.0, 1.1.0, and 2.0.0 when publishing 2.1.0:

```bash
/tmp/respawned-site-build/bin/python site/build.py \
  --distribution /absolute/path/to/qualified-2.1.0-distribution \
  --output /absolute/path/to/new-public-output \
  --archive-distribution /absolute/path/to/qualified-1.0.0-distribution \
  --archive-source 8d0512d4ff0d367b04ec028198195dc18f697c2a \
  --archive-distribution /absolute/path/to/qualified-1.1.0-distribution \
  --archive-source 25aded36e5c5357a3d9c7365d8dbd71695f8f386 \
  --archive-distribution /absolute/path/to/qualified-2.0.0-distribution \
  --archive-source 1f33f8f17762ad6fde3e528261cc9d6aa4630d85
```

Repeat each archive argument in matching order. The builder validates source revision, installer qualification, file sizes, hashes, and installer pins for every distribution. It copies only older versioned wheels and source archives; current installer, checksums, and package metadata stay current. Duplicate versions and artifacts from newer releases are rejected.

## Included files

The builder copies an explicit allowlist:

- Installer, current wheel and source archive, checksums, and public package manifest.
- Requested older wheels and source archives.
- Documentation HTML, styles, scripts, and verified demo media.
- `examples/outbox_client.py` at `/examples/outbox_client.py`.
- `docs/agent-prompt.txt` at `/agent-prompt.txt`.

Logs, credentials, internal notes, and unrelated output files are excluded. Downloads and their source files are hashed in `site-manifest.json`. The example and prompt are served as plain text.

The public `release.json` removes only build-time hosting flags; package identities and qualification evidence remain unchanged. Publication results belong in a separate report, not in immutable package metadata.

## Preview and verify

```bash
python3 -m http.server 8140 --bind 127.0.0.1 \
  --directory /absolute/path/to/new-preview
```

Check home, `/agent-integration/`, `/api/`, section links, mobile **Contents**, the demo, and download links. Verify the client and prompt downloads against the source hashes. Python's preview server does not apply Cloudflare headers or its custom 404 behavior.

## Deploy

Use `site/wrangler.jsonc` with the frozen output directory and `not_found_handling: "404-page"`. Scope deployment to the Respawned Worker and custom domain.

Merge reviewed source and confirm checks before building public assets. Upload those exact files, then verify live routes, 404 responses, headers, media, and package checksums. Record deployment identity and verification outside the package artifacts.

## Editing

`site/content/index.md` is the website installation and operation guide. Agent and API pages are built directly from `docs/AGENT_INTEGRATION.md` and `docs/API.md`.

Repository-relative links are explicitly mapped to public pages or revision-pinned sources. New unmapped links fail the build. Keep the README, browser guide, and API contract consistent.

Update demo files and `docs/media/demo-provenance.json` together. The builder rejects mismatched media; do not modify generated output after building.
