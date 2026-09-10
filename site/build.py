#!/usr/bin/env python3
"""Build static docs from checked-in sources and a qualified distribution."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from markdown_it import MarkdownIt


ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
ORIGIN = "https://respawned.williamshayden.com"
REPO = "https://github.com/williamshayden/respawned"
# Set this to the full application commit after its 2.0.0 artifacts qualify.
PACKAGE_COMMIT: str | None = None
PACKAGE_VERSION = "2.0.0"
ASSETS = ("docs.css", "docs.js", "favicon.svg")
SITE_SOURCES = ("build.py", "README.md", "requirements.txt", "_headers", "content/index.md")
TEXT_DOWNLOADS = {
    "examples/outbox_client.py": "examples/outbox_client.py",
    "docs/agent-prompt.txt": "agent-prompt.txt",
}


def distribution_files(version: str) -> tuple[str, str, str]:
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("Expected a release version with three numeric components")
    return (
        "install.sh",
        f"downloads/respawned-{version}-py3-none-any.whl",
        f"downloads/respawned-{version}.tar.gz",
    )


PUBLIC_FILES = distribution_files(PACKAGE_VERSION)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def regular_file(root: Path, name: str) -> Path:
    candidate = root / name
    if candidate.is_symlink() or not candidate.is_file() or not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Expected a regular file within {root}: {name}")
    return candidate


def validate_distribution(dist: Path, package_source: str | None, expected_version: str | None = PACKAGE_VERSION) -> dict:
    if not isinstance(package_source, str) or not re.fullmatch(r"[0-9a-f]{40}", package_source):
        raise ValueError("Expected an explicit full package source commit")
    release = json.loads(regular_file(dist, "release.json").read_text())
    version = release.get("version")
    if not isinstance(version, str) or (expected_version is not None and version != expected_version):
        raise ValueError(f"Distribution is not the required Respawned {expected_version or 'release'} version")
    public_files = distribution_files(version)
    if (release.get("name"), release.get("source_commit")) != ("respawned", package_source):
        raise ValueError("Distribution does not match the required Respawned source revision")
    if release.get("source_clean") is not True or release.get("validation", {}).get("installer_execution_verified") is not True:
        raise ValueError("Distribution must record a clean source and verified installer execution")
    artifacts = release.get("artifacts", [])
    if len(artifacts) != len(public_files) or {item["path"] for item in artifacts} != set(public_files):
        raise ValueError("Release manifest must contain exactly the qualified installer, wheel, and sdist")
    sums = {}
    for line in regular_file(dist, "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match or match[2] in sums:
            raise ValueError("Invalid or duplicate SHA256SUMS entry")
        sums[match[2]] = match[1]
    if set(sums) != set(public_files):
        raise ValueError("SHA256SUMS does not match the release file allowlist")
    for item in artifacts:
        path = regular_file(dist, item["path"])
        if digest(path) != item["sha256"] or digest(path) != sums[item["path"]] or path.stat().st_size != item["size_bytes"]:
            raise ValueError(f"Distribution size or hash mismatch: {item['path']}")
    wheel_hash = sums[public_files[1]]
    installer = (dist / "install.sh").read_text()
    expected_pins = (wheel_hash, ORIGIN, Path(public_files[1]).name, f'VERSION = "{version}"')
    if any(pin not in installer for pin in expected_pins):
        raise ValueError("Installer does not pin this distribution's version, wheel, and public origin")
    return release


def slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.lower())
    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", normalized)).strip("-")


def render_markdown(source: str, page: str, revision: str) -> tuple[str, list[tuple[str, str]]]:
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = parser.parse(source)
    sections = []
    used = set()
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            inline = tokens[index + 1]
            label = "".join(child.content for child in inline.children or [] if child.type in {"text", "code_inline"})
            anchor = slug(label)
            suffix = 1
            base = anchor
            while anchor in used:
                anchor = f"{base}-{suffix}"
                suffix += 1
            used.add(anchor)
            token.attrSet("id", anchor)
            if token.tag == "h2":
                sections.append((anchor, label))
        if token.type == "inline":
            for child in token.children or []:
                if child.type != "link_open":
                    continue
                href = child.attrGet("href") or ""
                mapping = {
                    "WEB_UI.md": "/",
                    "WEB_UI.md#connect-a-model-backend": "/#configure-a-drafting-backend",
                    "WEB_UI.md#server-and-api-access": "/#access-and-draft-version-tokens",
                    "WEB_UI.md#add-an-engine-connection": "/#connect-another-engine",
                    "WEB_UI.md#import-records-and-use-the-outbox": "/#review-and-outbox",
                    "WEB_UI.md#shared-application-boundary": f"{REPO}/blob/{revision}/docs/WEB_UI.md#shared-application-boundary",
                    "../README.md#install-and-start": "/#install-and-start-locally",
                    "../README.md": "/",
                    "API.md": "/api/",
                    "AGENT_INTEGRATION.md": "/agent-integration/",
                    "./api/": "/api/",
                    "./agent-integration/": "/agent-integration/",
                    "../src/respawned/core/contracts.py": f"{REPO}/blob/{revision}/src/respawned/core/contracts.py",
                    "../src/respawned/config/policy.yaml": f"{REPO}/blob/{revision}/src/respawned/config/policy.yaml",
                    "../src/respawned/core/policy.py": f"{REPO}/blob/{revision}/src/respawned/core/policy.py",
                    "../src/respawned/core/reasons.py": f"{REPO}/blob/{revision}/src/respawned/core/reasons.py",
                }
                if href in mapping:
                    child.attrSet("href", mapping[href])
                elif href.startswith("API.md#"):
                    child.attrSet("href", "/api/" + href[len("API.md"):])
                elif href.startswith("AGENT_INTEGRATION.md#"):
                    child.attrSet("href", "/agent-integration/" + href[len("AGENT_INTEGRATION.md"):])
                elif not href.startswith(("https://", "http://", "/", "#", "mailto:")):
                    raise ValueError(f"Unmapped {page} guide link: {href}")
    return parser.renderer.render(tokens, parser.options, {}), sections


def shell(title: str, body: str, path: str, sections: list[tuple[str, str]], revision: str) -> str:
    escape = html.escape
    nav = '<a href="/"' + (' aria-current="page"' if path == "/" else "") + '>Documentation</a>'
    nav += '<a href="/agent-integration/"' + (' aria-current="page"' if path == "/agent-integration/" else "") + '>Agent integration</a>'
    nav += '<a href="/api/"' + (' aria-current="page"' if path == "/api/" else "") + '>API reference</a>'
    toc = "".join(f'<a href="#{escape(anchor)}">{escape(label)}</a>' for anchor, label in sections)
    sidebar = f'<nav class="sidebar" aria-label="Documentation"><div class="page-links">{nav}</div><div class="section-links">{toc}</div></nav>'
    mobile = f'<details class="mobile-nav"><summary>Contents</summary><nav aria-label="Mobile documentation">{nav}<div class="section-links">{toc}</div></nav></details>'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · Respawned</title><meta name="description" content="Import records, review drafts, and connect approved messages to your sending tools through the Respawned API.">
<link rel="canonical" href="{ORIGIN}{escape(path)}"><link rel="stylesheet" href="/assets/docs.css"><link rel="icon" href="/assets/favicon.svg" type="image/svg+xml"><script src="/assets/docs.js" defer></script>
</head><body><a class="skip-link" href="#content">Skip to content</a>
<header class="site-header"><a class="brand" href="/">Respawned</a><nav aria-label="External links"><a href="https://williamshayden.com/" aria-label="Back to williamshayden.com" title="Back to williamshayden.com">← Portfolio</a><a href="{REPO}">GitHub <span aria-hidden="true">↗</span></a><a href="/install.sh">View installer</a></nav></header>
<div class="layout">{sidebar}<div class="reading-column">{mobile}<main id="content">{body}</main>
<footer><a href="{REPO}/tree/{revision}">Documentation source · {revision[:7]}</a><a href="/release.json">Package provenance</a></footer></div></div>
</body></html>'''


def build(
    distribution: Path,
    output: Path,
    package_source: str | None = PACKAGE_COMMIT,
    allow_dirty_preview: bool = False,
    *,
    archive_distribution: Path | list[Path] | None = None,
    archive_source: str | list[str] | None = None,
) -> None:
    distribution = distribution.resolve()
    output = output.absolute()
    if output.exists():
        raise ValueError("Output must be a new directory; existing artifacts are never overwritten")
    if output.resolve().is_relative_to(distribution) or distribution.is_relative_to(output.resolve()):
        raise ValueError("Output must be separate from the qualified distribution")
    release = validate_distribution(distribution, package_source)
    archive_dirs = ([archive_distribution] if isinstance(archive_distribution, Path)
                    else list(archive_distribution or []))
    archive_sources = ([archive_source] if isinstance(archive_source, str)
                       else list(archive_source or []))
    if len(archive_dirs) != len(archive_sources):
        raise ValueError("--archive-distribution and --archive-source must be supplied together")
    archived_distributions = []
    archive_files = []
    reserved_files = set(PUBLIC_FILES)
    for archive_dir, source_commit in zip(archive_dirs, archive_sources, strict=True):
        archive_dir = archive_dir.resolve()
        if output.resolve().is_relative_to(archive_dir) or archive_dir.is_relative_to(output.resolve()):
            raise ValueError("Output must be separate from the archived distribution")
        archive = validate_distribution(archive_dir, source_commit, expected_version=None)
        names = distribution_files(archive["version"])[1:]
        if set(names) & reserved_files:
            raise ValueError("Archived distribution duplicates a version or artifact target")
        if tuple(map(int, archive["version"].split("."))) >= tuple(map(int, PACKAGE_VERSION.split("."))):
            raise ValueError("Archived distribution must be older than the current release")
        reserved_files.update(names)
        archive_files.extend((archive_dir, name) for name in names)
        archived_distributions.append({
            "version": archive["version"],
            "source_commit": source_commit,
            "qualified_distribution_release_sha256": digest(archive_dir / "release.json"),
            "artifacts": [
                {key: item[key] for key in ("path", "sha256", "size_bytes")}
                for item in archive["artifacts"] if item["path"] in names
            ],
        })
    revision = git("rev-parse", "HEAD")
    source_files = [regular_file(SITE, name) for name in SITE_SOURCES]
    source_files += [regular_file(SITE / "assets", name) for name in ASSETS]
    if (SITE / "wrangler.jsonc").exists():
        source_files.append(regular_file(SITE, "wrangler.jsonc"))
    source_files += [regular_file(ROOT, name) for name in ("docs/AGENT_INTEGRATION.md", "docs/API.md", "docs/WEB_UI.md", "docs/media/demo-provenance.json")]
    source_files += [regular_file(ROOT, name) for name in TEXT_DOWNLOADS]
    pages = [
        (SITE / "content/index.md", "index.html", "/", "Documentation", "index"),
        (ROOT / "docs/AGENT_INTEGRATION.md", "agent-integration/index.html", "/agent-integration/", "Agent integration", "agent"),
        (ROOT / "docs/API.md", "api/index.html", "/api/", "API reference", "api"),
    ]
    rendered = []
    for source, target, url, title, key in pages:
        markdown = source.read_text(encoding="utf-8")
        if key == "agent":
            markdown = markdown.replace("# Connect your own agent\n", "# Agent integration\n", 1)
        body, sections = render_markdown(markdown, key, revision)
        if key == "index":
            video = '''<details class="demo"><summary>Product demo</summary><video muted loop playsinline preload="none" width="1920" height="1080" poster="/media/respawned-demo-poster.png" aria-label="Respawned with live Codex, mock correspondence, and local test delivery" data-src="/media/respawned-demo.mp4"></video><p>Codex drafts from mock correspondence. The recording includes browser edits, CLI output, and delivery to a local test HTTP endpoint followed by a receipt. No external provider is contacted. <a href="/media/respawned-demo.mp4">Open video</a></p></details>'''
            position = body.find('<h2')
            body = body[:position] + video + body[position:]
        rendered.append((target, shell(title, body, url, sections, revision)))
    provenance = json.loads((ROOT / "docs/media/demo-provenance.json").read_text())
    demo = next(item for item in provenance["media"] if item["file"] == "respawned-demo.mp4")
    media = [("respawned-demo.mp4", demo["sha256"]), ("respawned-demo-poster.png", demo["poster_sha256"])]
    for name, expected in media:
        source = regular_file(ROOT / "docs/media", name)
        if digest(source) != expected:
            raise ValueError(f"Demo media does not match repository provenance: {name}")
        source_files.append(source)
    if not allow_dirty_preview:
        if git("status", "--porcelain"):
            raise ValueError("Publication builds require a clean checkout; --allow-dirty-preview is for local QA only")
        for source in source_files:
            name = str(source.relative_to(ROOT))
            regular_file(ROOT, name)
            committed = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{revision}:{name}"])
            if hashlib.sha256(committed).hexdigest() != digest(source):
                raise ValueError(f"Source does not match committed revision: {name}")
    output.mkdir(parents=True)
    for target, contents in rendered:
        path = output / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    not_found = '<h1>Page not found</h1><p>This documentation page does not exist.</p><p><a href="/">Open the documentation</a> or <a href="/agent-integration/">read the agent integration guide</a>.</p>'
    (output / "404.html").write_text(shell("Page not found", not_found, "/404.html", [], revision), encoding="utf-8")
    for name in (*PUBLIC_FILES, "SHA256SUMS"):
        target = output / name
        target.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(distribution / name, target)
    for archive_dir, name in archive_files:
        target = output / name
        if target.exists():
            raise ValueError(f"Archived artifact would replace an existing output: {name}")
        shutil.copyfile(archive_dir / name, target)
    for source, destination in TEXT_DOWNLOADS.items():
        target = output / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(regular_file(ROOT, source), target)
    public_release = json.loads(json.dumps(release))
    public_release.pop("published", None)
    public_release.get("validation", {}).pop("public_hosting_verified", None)
    (output / "release.json").write_text(json.dumps(public_release, indent=2) + "\n")
    for name, _ in media:
        target = output / "media" / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "docs/media" / name, target)
    (output / "assets").mkdir()
    for name in ASSETS:
        shutil.copyfile(regular_file(SITE / "assets", name), output / "assets" / name)
    shutil.copyfile(SITE / "_headers", output / "_headers")
    (output / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {ORIGIN}/sitemap.xml\n")
    sitemap_urls = "".join(f"<url><loc>{ORIGIN}{url}</loc></url>" for _, _, url, _, _ in pages)
    (output / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sitemap_urls}</urlset>')
    manifest = {
        "site": ORIGIN,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "docs_source_commit": revision,
        "docs_source_clean": not allow_dirty_preview,
        "package_source_commit": release["source_commit"],
        "archived_distributions": archived_distributions,
        "qualified_distribution_release_sha256": digest(distribution / "release.json"),
        "public_release_sha256": digest(output / "release.json"),
        "public_release_transform": "Omit only build-time published and validation.public_hosting_verified booleans; artifact identities and qualification evidence are unchanged",
        "sources": {str(path.relative_to(ROOT)): digest(path) for path in source_files},
        "files": {str(path.relative_to(output)): {"sha256": digest(path), "size_bytes": path.stat().st_size} for path in sorted(output.rglob("*")) if path.is_file()},
    }
    (output / "site-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"output": str(output), "docs_source_commit": revision, "package_source_commit": release["source_commit"], "files": len(manifest["files"]) + 1}, indent=2))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--distribution", required=True, type=Path)
    cli.add_argument("--output", required=True, type=Path)
    cli.add_argument("--package-source", default=PACKAGE_COMMIT, help="Full qualified package commit; required until PACKAGE_COMMIT is pinned. CI may require its own exact HEAD.")
    cli.add_argument("--archive-distribution", type=Path, action="append", help="Qualified older distribution to preserve. Repeat with a matching --archive-source for every prior release.")
    cli.add_argument("--archive-source", action="append", help="Full source commit for each --archive-distribution, in matching order.")
    cli.add_argument("--allow-dirty-preview", action="store_true", help="Permit local preview sources; marks the output unqualified for publication")
    args = cli.parse_args()
    try:
        build(
            args.distribution, args.output, args.package_source, args.allow_dirty_preview,
            archive_distribution=args.archive_distribution, archive_source=args.archive_source,
        )
    except (ValueError, KeyError, OSError, StopIteration, subprocess.CalledProcessError) as exc:
        cli.exit(1, f"Build failed: {exc}\n")
