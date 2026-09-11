# Releasing Respawned

Release one qualified wheel and source archive through every enabled channel. Published versioned files are immutable. Use a new package version when code, package metadata, or its included README changes.

## Prepare the version

Update `pyproject.toml`, the project entry in `uv.lock`, `CHANGELOG.md`, and current download links. The engine reports its installed package version. When frontend sources change, rebuild and commit the bundled UI.

Set `PACKAGE_VERSION` in `site/build.py` to the new version and leave `PACKAGE_COMMIT` unset until qualification. CI builds with an explicit source revision. Public site builds require the final qualified source pin and a clean documentation checkout.

Merge the reviewed change after **Checks** passes. Create a `vMAJOR.MINOR.PATCH` tag on its qualified application commit; that commit must be an ancestor of `main` and contain the matching package version. Do not move a release tag.

## GitHub release

Run **Release** from the version tag. For example:

```sh
gh workflow run release.yml --ref v2.1.0 -f publish-pypi=false
```

The workflow runs the application, Docker, browser, distribution, installer, and documentation checks. It transfers the qualified files between jobs using a GitHub Actions artifact with digest verification. Publishing jobs do not rebuild packages.

The GitHub job creates a draft, uploads the wheel, source archive, installer, checksums, and package provenance, then downloads and verifies every file before publishing. A matching rerun preserves existing files. An incomplete owned draft can finish uploading; a conflicting published file is never replaced. GitHub checksums use flat asset names, while the website distribution retains its `downloads/` directory.

## PyPI Trusted Publishing

Configure a pending publisher under the account that will own the `respawned` project:

| Field | Value |
| --- | --- |
| PyPI project | `respawned` |
| GitHub owner | `williamshayden` |
| Repository | `respawned` |
| Workflow filename | `release.yml` |
| GitHub environment | `pypi` |

Create the matching `pypi` environment in the GitHub repository. PyPI account setup and the pending publisher must be complete before enabling registry publication. A pending publisher does not reserve the project name. See [PyPI setup](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) and [publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

Run the same release workflow on the version tag with `publish-pypi=true`. The registry job receives only the qualified wheel and source archive and uses short-lived GitHub OIDC credentials. Attestations are enabled. No stored PyPI API token is needed.

After publishing, verify the PyPI project/version, both file hashes, attestations, and a fresh `pip install respawned==VERSION`. Check `respawned --version`, `respawned status`, and the packaged UI. Do not advertise the registry command before this succeeds. If publication is interrupted, inspect the registry before retrying; do not replace or rebuild an already published version.

## Website downloads

Download the qualified files from the release run and verify their identities. Pin their application commit in `site/build.py`, then build the site from clean merged documentation source using [the site build guide](../site/README.md). Preserve every previously published versioned wheel and source archive.

Deploy the frozen assets, verify live hashes and documentation links, and test the public installer with both a fresh prefix and an upgrade from the preceding release. Keep PostgreSQL and external configuration separate from installation prefixes. Record the deployment identity and public verification outside the immutable package manifest.

When a later documentation commit changes only website content, continue serving the existing qualified packages. Refresh demo media only when the walkthrough itself changes.
