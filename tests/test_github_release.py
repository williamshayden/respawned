"""Qualify release files and exercise publishing without real GitHub writes."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest

SOURCE = "a" * 40
VERSION = "2.1.0"
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("github_release", ROOT / "scripts/releases/github_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def distribution(tmp_path):
    (tmp_path / "downloads").mkdir()
    wheel = tmp_path / "downloads" / f"respawned-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"respawned-{VERSION}.dist-info/METADATA",
                         f"Metadata-Version: 2.4\nName: respawned\nVersion: {VERSION}\nRequires-Python: >=3.12\n\n")
    source = tmp_path / "downloads" / f"respawned-{VERSION}.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        body = f'[project]\nname = "respawned"\nversion = "{VERSION}"\n'.encode()
        member = tarfile.TarInfo(f"respawned-{VERSION}/pyproject.toml")
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    installer = tmp_path / "install.sh"
    installer.write_text(f'VERSION = "{VERSION}"\n{wheel.name}\n{release.digest(wheel.read_bytes())}\n')
    artifacts = [{"path": path.relative_to(tmp_path).as_posix(), "size_bytes": path.stat().st_size,
                  "sha256": release.digest(path.read_bytes())} for path in (installer, wheel, source)]
    manifest = {"name": "respawned", "version": VERSION, "source_commit": SOURCE,
                "source_clean": True, "published": False, "prepared_at": "2026-09-11T01:00:00Z",
                "artifacts": artifacts,
                "validation": {"installer_execution_verified": True,
                               "bundled_ui_identical_in_wheel_and_sdist": True,
                               "installer_checks_passed": 14, "public_hosting_verified": False}}
    (tmp_path / "release.json").write_text(json.dumps(manifest))
    (tmp_path / "SHA256SUMS").write_text("".join(f"{item['sha256']}  {item['path']}\n" for item in artifacts))
    return tmp_path


def test_packages_remain_identical_and_flat_checksums_are_usable(distribution):
    files = release.qualified_files(distribution, VERSION, SOURCE)
    assert files[f"respawned-{VERSION}-py3-none-any.whl"] == (distribution / "downloads" / f"respawned-{VERSION}-py3-none-any.whl").read_bytes()
    assert files[f"respawned-{VERSION}.tar.gz"] == (distribution / "downloads" / f"respawned-{VERSION}.tar.gz").read_bytes()
    for line in files["SHA256SUMS"].decode().splitlines():
        checksum, name = line.split("  ")
        assert "/" not in name and hashlib.sha256(files[name]).hexdigest() == checksum
    public = json.loads(files["release.json"])
    assert "published" not in public and "public_hosting_verified" not in public["validation"]
    assert public["source_commit"] == SOURCE


@pytest.mark.parametrize("field,value", [("source_clean", False), ("source_commit", "b" * 40), ("version", "2.0.0")])
def test_rejects_another_or_dirty_source(distribution, field, value):
    path = distribution / "release.json"
    document = json.loads(path.read_text())
    document[field] = value
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="clean, tagged"):
        release.qualified_files(distribution, VERSION, SOURCE)


def test_rejects_unqualified_installer(distribution):
    path = distribution / "release.json"
    document = json.loads(path.read_text())
    document["validation"]["installer_execution_verified"] = False
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="qualification"):
        release.qualified_files(distribution, VERSION, SOURCE)


def test_rejects_altered_package(distribution):
    wheel = distribution / "downloads" / f"respawned-{VERSION}-py3-none-any.whl"
    wheel.write_bytes(wheel.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed after qualification"):
        release.qualified_files(distribution, VERSION, SOURCE)


def test_rejects_extra_file_and_checksum_changes(distribution):
    extra = distribution / "credentials.json"
    extra.write_text("fixture")
    with pytest.raises(ValueError, match="unexpected or missing"):
        release.qualified_files(distribution, VERSION, SOURCE)
    extra.unlink()
    (distribution / "SHA256SUMS").write_text("different checksums")
    with pytest.raises(ValueError, match="checksum inventory"):
        release.qualified_files(distribution, VERSION, SOURCE)


def test_rejects_symlink_artifact(distribution, tmp_path_factory):
    unrelated = tmp_path_factory.mktemp("outside") / "installer"
    installer = distribution / "install.sh"
    unrelated.write_bytes(installer.read_bytes())
    installer.unlink()
    installer.symlink_to(unrelated)
    with pytest.raises(ValueError, match="Symlinks"):
        release.qualified_files(distribution, VERSION, SOURCE)
    assert unrelated.is_file()


@pytest.mark.parametrize("ref,source,repository", [
    ("refs/heads/main", SOURCE, release.REPOSITORY),
    ("refs/tags/v2.1.0;echo", SOURCE, release.REPOSITORY),
    ("refs/tags/v2.1.0rc1", SOURCE, release.REPOSITORY),
    ("refs/tags/v2.1.0", "main", release.REPOSITORY),
    ("refs/tags/v2.1.0", SOURCE, "someone/fork"),
])
def test_release_identity_requires_canonical_tag_and_commit(ref, source, repository):
    with pytest.raises(ValueError):
        release.release_identity({"GITHUB_REF": ref, "GITHUB_SHA": source, "GITHUB_REPOSITORY": repository})


def test_existing_release_may_only_differ_in_build_timestamp(distribution):
    files = release.qualified_files(distribution, VERSION, SOURCE)
    old = json.loads(files["release.json"])
    old["prepared_at"] = "2026-09-10T00:00:00Z"
    assert release.matching_asset("release.json", files["release.json"], json.dumps(old).encode())
    old["artifacts"][0]["sha256"] = "b" * 64
    assert not release.matching_asset("release.json", files["release.json"], json.dumps(old).encode())
    assert not release.matching_asset("install.sh", files["install.sh"], b"replacement")


class FakeGitHub:
    """Small stateful GitHub boundary: paginated records and downloaded bytes."""

    def __init__(self, files, notes):
        self.files = files
        self.notes = notes
        self.tag = f"v{VERSION}"
        self.url = f"https://github.com/{release.REPOSITORY}/releases/tag/{self.tag}"
        self.record = None
        self.assets = {}
        self.calls = []
        self.events = []
        self.downloads = set()

    def seed(self, *, draft, assets):
        self.record = {"id": 42, "tag_name": self.tag, "target_commitish": SOURCE,
                       "name": f"Respawned {VERSION}", "body": self.notes,
                       "draft": draft, "html_url": self.url}
        self.assets = dict(assets)

    def document(self):
        if self.record is None:
            return None
        return {**self.record, "assets": [
            {"id": index, "name": name, "size": len(body), "state": "uploaded"}
            for index, (name, body) in enumerate(self.assets.items(), 1)
        ]}

    @staticmethod
    def option(arguments, name):
        return arguments[arguments.index(name) + 1]

    def __call__(self, *arguments, missing_ok=False):
        self.calls.append(arguments)
        if arguments[0] == "api":
            endpoint = arguments[1]
            if endpoint == f"repos/{release.REPOSITORY}/git/ref/tags/{self.tag}":
                return json.dumps({"object": {"type": "commit", "sha": SOURCE}})
            assert endpoint == f"repos/{release.REPOSITORY}/releases?per_page=100", (
                "Draft discovery must use the authenticated release list, not the published-only tag endpoint",
                endpoint,
            )
            assert "--paginate" in arguments and "--slurp" in arguments
            # A similarly named release precedes the exact tag on another page.
            unrelated = {"id": 7, "tag_name": self.tag + "-rc1", "name": "Unrelated release",
                         "draft": False, "body": "Other notes", "assets": []}
            return json.dumps([[unrelated], [self.document()] if self.record else []])

        assert arguments[0] == "release", arguments
        operation, tag = arguments[1:3]
        assert tag == self.tag
        assert self.option(arguments, "--repo") == release.REPOSITORY
        if operation == "create":
            assert self.record is None, "Do not create over an existing draft"
            assert "--draft" in arguments and "--verify-tag" in arguments
            assert self.option(arguments, "--target") == SOURCE
            assert self.option(arguments, "--title") == f"Respawned {VERSION}"
            assert Path(self.option(arguments, "--notes-file")).read_text() == self.notes
            self.seed(draft=True, assets={})
            self.events.append(("create", None))
            return self.url
        if operation == "upload":
            assert self.record is not None and self.record["draft"]
            assert "--clobber" not in arguments
            path = Path(arguments[3])
            assert path.name not in self.assets, "Existing asset bytes must be preserved"
            self.assets[path.name] = path.read_bytes()
            self.events.append(("upload", path.name))
            return ""
        if operation == "download":
            name = self.option(arguments, "--pattern")
            target = Path(self.option(arguments, "--dir")) / name
            target.write_bytes(self.assets[name])
            self.downloads.add(name)
            self.events.append(("download", name))
            return ""
        if operation == "edit":
            assert self.record is not None and self.record["draft"]
            assert "--draft=false" in arguments
            assert set(self.assets) == set(self.files)
            assert self.downloads == set(self.files), "Publish only after downloading every remote asset for verification"
            self.record["draft"] = False
            self.events.append(("publish", None))
            return ""
        raise AssertionError(f"Unexpected GitHub operation: {arguments}")


@pytest.fixture
def publisher(distribution, monkeypatch):
    files = release.qualified_files(distribution, VERSION, SOURCE)
    notes = f"Fixture release notes.\n\nSource revision: `{SOURCE}`.\n"
    github = FakeGitHub(files, notes)
    monkeypatch.setattr(release, "release_notes", lambda _version, _source: notes)
    monkeypatch.setattr(release, "gh", github)
    return files, github


def test_release_lookup_finds_exact_draft_on_later_page(publisher):
    _files, github = publisher
    github.seed(draft=True, assets={})

    found = release.release_view(github.tag)

    assert found["id"] == 42 and found["draft"] is True
    assert found["tag_name"] == github.tag
    assert any(call[0] == "api" and call[1] == f"repos/{release.REPOSITORY}/releases?per_page=100"
               and "--paginate" in call and "--slurp" in call for call in github.calls)
    assert not any("/releases/tags/" in argument for call in github.calls for argument in call)


def test_first_publication_creates_draft_verifies_all_assets_then_publishes(publisher):
    files, github = publisher

    url = release.publish(github.tag, VERSION, SOURCE, files)

    assert url == github.url
    assert github.record["draft"] is False
    assert github.assets == files
    assert sum(event == ("create", None) for event in github.events) == 1
    assert sum(event == ("publish", None) for event in github.events) == 1
    assert {name for operation, name in github.events if operation == "upload"} == set(files)
    published = github.events.index(("publish", None))
    assert all(index < published for index, (operation, _name) in enumerate(github.events) if operation == "download")


def test_resume_owned_partial_draft_preserves_existing_assets(publisher):
    files, github = publisher
    previous_manifest = json.loads(files["release.json"])
    previous_manifest["prepared_at"] = "2026-09-10T00:00:00Z"
    existing = {f"respawned-{VERSION}-py3-none-any.whl": files[f"respawned-{VERSION}-py3-none-any.whl"],
                "release.json": json.dumps(previous_manifest).encode()}
    github.seed(draft=True, assets=existing)

    url = release.publish(github.tag, VERSION, SOURCE, files)

    assert url == github.url and github.record["draft"] is False
    assert github.assets == {**files, **existing}
    assert ("create", None) not in github.events
    assert {name for operation, name in github.events if operation == "upload"} == set(files) - set(existing)
    assert github.downloads == set(files)


def test_conflicting_published_asset_preserves_release_without_mutations(publisher):
    files, github = publisher
    existing = {**files, f"respawned-{VERSION}-py3-none-any.whl": b"already-published different wheel"}
    github.seed(draft=False, assets=existing)
    before = github.document()

    with pytest.raises(ValueError, match="Existing release asset differs; refusing replacement"):
        release.publish(github.tag, VERSION, SOURCE, files)

    assert github.assets == existing
    assert github.document() == before
    assert not any(operation in {"create", "upload", "publish"} for operation, _name in github.events)
    assert not any(call[:2] == ("release", "edit") for call in github.calls)
