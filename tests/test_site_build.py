"""Static publication rules, using small synthetic artifact inventories."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("respawned_site_build", ROOT / "site/build.py")
site_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_build)
CURRENT_VERSION = site_build.PACKAGE_VERSION


def sha(data):
    return hashlib.sha256(data).hexdigest()


def distribution(directory, version, source):
    """Builder fixtures only; these bytes are not executable Python packages."""
    directory.mkdir()
    (directory / "downloads").mkdir()
    wheel = f"downloads/respawned-{version}-py3-none-any.whl"
    sdist = f"downloads/respawned-{version}.tar.gz"
    wheel_body = f"fixture wheel {version}".encode()
    contents = {
        wheel: wheel_body,
        sdist: f"fixture sdist {version}".encode(),
        "install.sh": (
            f'VERSION = "{version}"\n{Path(wheel).name}\n'
            f"{sha(wheel_body)}\n{site_build.ORIGIN}\n"
        ).encode(),
    }
    for name, body in contents.items():
        (directory / name).write_bytes(body)
    artifacts = [{"path": name, "sha256": sha(body), "size_bytes": len(body)}
                 for name, body in contents.items()]
    (directory / "SHA256SUMS").write_text("".join(
        f"{item['sha256']}  {item['path']}\n" for item in artifacts
    ))
    (directory / "release.json").write_text(json.dumps({
        "name": "respawned", "version": version, "source_commit": source,
        "source_clean": True, "published": False,
        "validation": {"installer_execution_verified": True, "public_hosting_verified": False},
        "artifacts": artifacts,
    }))
    return directory


@pytest.fixture
def source(monkeypatch, tmp_path):
    root = tmp_path / "source"
    sources = {
        "site/content/index.md": "# Documentation\n\n## Review and outbox\n\n[Prompt](/agent-prompt.txt)\n",
        "docs/AGENT_INTEGRATION.md": "# Agent integration\n\n[API](API.md)\n",
        "docs/WEB_UI.md": "# Browser guide\n",
        "docs/API.md": "# API reference\n\n[Agent](AGENT_INTEGRATION.md)\n"
                       "[Review](WEB_UI.md#import-records-and-use-the-outbox)\n",
        "docs/media/respawned-demo.mp4": "fixture video",
        "docs/media/respawned-demo-poster.png": "fixture poster",
        "examples/outbox_client.py": "#!/usr/bin/env python3\nprint('fixture client')\n",
        "docs/agent-prompt.txt": "Use this fixture prompt.\n",
        "examples/not-public.txt": "This file must never be copied.\n",
    }
    for name in site_build.SITE_SOURCES:
        sources.setdefault("site/" + name, "fixture")
    for name in site_build.ASSETS:
        sources["site/assets/" + name] = "fixture"
    for name, body in sources.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    (root / "docs/media/demo-provenance.json").write_text(json.dumps({
        "media": [{"file": "respawned-demo.mp4",
                   "sha256": sha(b"fixture video"), "poster_sha256": sha(b"fixture poster")}],
    }))
    monkeypatch.setattr(site_build, "ROOT", root)
    monkeypatch.setattr(site_build, "SITE", root / "site")
    monkeypatch.setattr(site_build, "git", lambda *_args: "d" * 40)
    return root


def test_build_preserves_older_releases_and_allowlisted_downloads(source, tmp_path):
    current = distribution(tmp_path / "current", CURRENT_VERSION, "a" * 40)
    first = distribution(tmp_path / "first", "1.0.0", "b" * 40)
    second = distribution(tmp_path / "second", "1.1.0", "c" * 40)
    third = distribution(tmp_path / "third", "2.0.0", "e" * 40)
    output = tmp_path / "public"
    site_build.build(current, output, "a" * 40, True,
                     archive_distribution=[first, second, third],
                     archive_source=["b" * 40, "c" * 40, "e" * 40])
    manifest = json.loads((output / "site-manifest.json").read_text())
    assert manifest["docs_source_clean"] is False
    assert [item["version"] for item in manifest["archived_distributions"]] == ["1.0.0", "1.1.0", "2.0.0"]
    for directory, version in ((current, CURRENT_VERSION), (first, "1.0.0"), (second, "1.1.0"), (third, "2.0.0")):
        for name in site_build.distribution_files(version)[1:]:
            assert (output / name).read_bytes() == (directory / name).read_bytes()
    assert (output / "install.sh").read_bytes() == (current / "install.sh").read_bytes()
    release = json.loads((output / "release.json").read_text())
    assert release["version"] == CURRENT_VERSION and "published" not in release
    assert release["validation"] == {"installer_execution_verified": True}
    for original, target in site_build.TEXT_DOWNLOADS.items():
        assert (output / target).read_bytes() == (source / original).read_bytes()
        assert manifest["sources"][original] == sha((source / original).read_bytes())
        assert manifest["files"][target]["sha256"] == manifest["sources"][original]
    assert not (output / "examples/not-public.txt").exists()
    api = (output / "api/index.html").read_text()
    assert 'href="/agent-integration/"' in api
    assert 'href="/#review-and-outbox"' in api


@pytest.mark.parametrize("versions,match", [
    (["1.1.0", "1.1.0"], "duplicates"),
    ([CURRENT_VERSION], "duplicates"),
    (["3.0.0"], "older"),
])
def test_archive_versions_cannot_replace_or_duplicate_downloads(source, tmp_path, versions, match):
    current = distribution(tmp_path / "current", CURRENT_VERSION, "a" * 40)
    archives = [distribution(tmp_path / f"archive-{index}", version, "b" * 40)
                for index, version in enumerate(versions)]
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match=match):
        site_build.build(current, output, "a" * 40, True,
                         archive_distribution=archives, archive_source=["b" * 40] * len(archives))
    assert not output.exists()


def test_archive_sources_must_match_every_input(source, tmp_path):
    current = distribution(tmp_path / "current", CURRENT_VERSION, "a" * 40)
    first = distribution(tmp_path / "first", "1.0.0", "b" * 40)
    with pytest.raises(ValueError, match="supplied together"):
        site_build.build(current, tmp_path / "missing", "a" * 40, True,
                         archive_distribution=[first], archive_source=[])
    with pytest.raises(ValueError, match="source revision"):
        site_build.build(current, tmp_path / "wrong", "a" * 40, True,
                         archive_distribution=first, archive_source="c" * 40)


def test_unset_source_pin_and_wrong_current_version_fail_closed(tmp_path):
    current = distribution(tmp_path / "current", CURRENT_VERSION, "a" * 40)
    with pytest.raises(ValueError, match="explicit full"):
        site_build.validate_distribution(current, None)
    previous = distribution(tmp_path / "previous", "1.1.0", "b" * 40)
    with pytest.raises(ValueError, match=CURRENT_VERSION):
        site_build.validate_distribution(previous, "b" * 40)


def test_symlink_download_is_rejected_before_output(source, tmp_path):
    current = distribution(tmp_path / "current", CURRENT_VERSION, "a" * 40)
    prompt = source / "docs/agent-prompt.txt"
    prompt.unlink()
    external = tmp_path / "private.txt"
    external.write_text("Do not publish")
    prompt.symlink_to(external)
    with pytest.raises(ValueError, match="regular file"):
        site_build.build(current, tmp_path / "rejected", "a" * 40, True)
    assert not (tmp_path / "rejected").exists()


def test_repository_guides_render_with_current_link_map():
    for name, page in (("site/content/index.md", "index"),
                       ("docs/AGENT_INTEGRATION.md", "agent"), ("docs/API.md", "api")):
        rendered, _sections = site_build.render_markdown((ROOT / name).read_text(), page, "d" * 40)
        assert "<h1" in rendered
