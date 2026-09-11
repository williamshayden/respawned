"""Publish qualified packages to a versioned GitHub release without replacing files."""
from __future__ import annotations

import argparse
import copy
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import tomllib
from urllib.parse import quote
import zipfile

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "williamshayden/respawned"
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SHA = re.compile(r"[0-9a-f]{40}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *arguments], text=True).strip()


def release_identity(environment: dict[str, str]) -> tuple[str, str, str]:
    require(environment.get("GITHUB_REPOSITORY") == REPOSITORY, "Release only from the canonical repository")
    ref, source = environment.get("GITHUB_REF", ""), environment.get("GITHUB_SHA", "")
    tag = ref.removeprefix("refs/tags/")
    version = tag.removeprefix("v")
    require(ref == "refs/tags/v" + version and VERSION.fullmatch(version) is not None,
            "Run the release workflow on a stable vMAJOR.MINOR.PATCH tag")
    require(SHA.fullmatch(source) is not None, "Release source must be a full commit SHA")
    return tag, version, source


def validate_source(environment: dict[str, str], *, check_merged: bool) -> tuple[str, str, str]:
    tag, version, source = release_identity(environment)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    require(project["name"] == "respawned" and project["version"] == version,
            "The tag must match the committed package version")
    require(git("rev-parse", "HEAD") == source, "Checkout differs from the workflow's immutable source")
    if check_merged:
        require(git("rev-parse", tag + "^{commit}") == source, "The release tag moved")
        merged = subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", source, "origin/main"])
        require(merged.returncode == 0, "Release source must already be merged into main")
    return tag, version, source


def read_regular(root: Path, relative: str) -> bytes:
    path = root / relative
    require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()),
            f"Expected a regular artifact inside the distribution: {relative}")
    require(path.stat().st_size <= 50 * 1024 * 1024, f"Artifact exceeds the release size bound: {relative}")
    return path.read_bytes()


def qualified_files(root: Path, version: str, source: str) -> dict[str, bytes]:
    """Return flat GitHub assets; package bytes stay identical to the CI distribution."""
    manifest = json.loads(read_regular(root, "release.json"))
    require(manifest.get("name") == "respawned" and manifest.get("version") == version and
            manifest.get("source_commit") == source and manifest.get("source_clean") is True,
            "Distribution does not match the clean, tagged release source")
    validation = manifest.get("validation", {})
    require(validation.get("installer_execution_verified") is True and
            validation.get("bundled_ui_identical_in_wheel_and_sdist") is True and
            type(validation.get("installer_checks_passed")) is int and validation["installer_checks_passed"] >= 14,
            "Distribution has not completed package and installer qualification")
    names = {"install.sh", f"downloads/respawned-{version}-py3-none-any.whl",
             f"downloads/respawned-{version}.tar.gz"}
    entries = manifest.get("artifacts", [])
    require(len(entries) == 3 and {item.get("path") for item in entries} == names,
            "Unexpected artifact inventory")
    expected_files = names | {"release.json", "SHA256SUMS"}
    for path in root.rglob("*"):
        require(not path.is_symlink(), "Symlinks are not release artifacts")
        require(path.is_file() or path.is_dir(), "Non-regular release artifact")
    require({path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()} == expected_files,
            "The downloaded release artifact contains unexpected or missing files")
    bodies = {}
    for item in entries:
        body = read_regular(root, item["path"])
        require(len(body) == item.get("size_bytes") and digest(body) == item.get("sha256"),
                f"Artifact changed after qualification: {item['path']}")
        bodies[item["path"]] = body
    expected_sums = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries).encode()
    require(read_regular(root, "SHA256SUMS") == expected_sums, "Qualified checksum inventory differs")
    wheel_name = f"downloads/respawned-{version}-py3-none-any.whl"
    with zipfile.ZipFile(root / wheel_name) as archive:
        metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        require(len(metadata_names) == 1, "Wheel metadata is ambiguous")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        require(metadata["Name"] == "respawned" and metadata["Version"] == version and
                metadata["Requires-Python"] == ">=3.12", "Wheel metadata differs from the release")
    with tarfile.open(root / f"downloads/respawned-{version}.tar.gz") as archive:
        member = archive.extractfile(f"respawned-{version}/pyproject.toml")
        require(member is not None, "Source archive lacks project metadata")
        project = tomllib.loads(member.read().decode())["project"]
        require(project["name"] == "respawned" and project["version"] == version,
                "Source archive metadata differs from the release")
    installer = bodies["install.sh"].decode("utf-8")
    require(all(value in installer for value in (f'VERSION = "{version}"', Path(wheel_name).name,
                                                 digest(bodies[wheel_name]))), "Installer does not pin the qualified wheel")
    public = copy.deepcopy(manifest)
    public.pop("published", None)
    public["validation"].pop("public_hosting_verified", None)
    result = {Path(name).name: body for name, body in bodies.items()}
    # GitHub assets have flat names; checksums describe the files users download.
    result["SHA256SUMS"] = "".join(f"{digest(body)}  {name}\n" for name, body in result.items()).encode()
    result["release.json"] = (json.dumps(public, indent=2) + "\n").encode()
    return result


def matching_asset(name: str, expected: bytes, actual: bytes) -> bool:
    if name != "release.json":
        return actual == expected
    # Requalification may change the preparation time; preserve the first record.
    first, second = json.loads(expected), json.loads(actual)
    first.pop("prepared_at", None)
    second.pop("prepared_at", None)
    return first == second


def release_notes(version: str, source: str) -> str:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = "## " + version + "\n"
    require(changelog.count(heading) == 1, "Release needs one matching changelog entry")
    notes = changelog.split(heading, 1)[1].split("\n## ", 1)[0].strip()
    require(bool(notes), "Release notes are empty")
    return (notes + "\n\nThe wheel and source archive passed the application, browser, distribution, and installer checks. "
            "GitHub and website packages use the same qualified bytes.\n\n"
            f"Source revision: `{source}`.\n\n"
            "[Installation and upgrade guide](https://respawned.williamshayden.com/).\n")


def gh(*arguments: str, missing_ok: bool = False) -> str | None:
    completed = subprocess.run(["gh", *arguments], capture_output=True, text=True, timeout=180)
    if completed.returncode:
        if missing_ok and "(HTTP 404)" in completed.stderr:
            return None
        raise RuntimeError("GitHub operation failed: " + completed.stderr.strip()[:400])
    return completed.stdout


def release_view(tag: str) -> dict | None:
    # The by-tag REST endpoint excludes drafts. Authenticated listing includes
    # our draft so an interrupted upload can resume before it becomes public.
    pages = json.loads(gh("api", f"repos/{REPOSITORY}/releases?per_page=100", "--paginate", "--slurp"))
    matches = [entry for page in pages for entry in page if entry["tag_name"] == tag]
    require(len(matches) <= 1, "Multiple releases use the same tag")
    return matches[0] if matches else None


def verify_remote_tag(tag: str, source: str) -> None:
    reference = json.loads(gh("api", f"repos/{REPOSITORY}/git/ref/tags/{quote(tag, safe='')}"))["object"]
    if reference["type"] == "tag":
        reference = json.loads(gh("api", f"repos/{REPOSITORY}/git/tags/{reference['sha']}"))["object"]
    require(reference["type"] == "commit" and reference["sha"] == source, "Remote release tag moved")


def publish(tag: str, version: str, source: str, files: dict[str, bytes]) -> str:
    verify_remote_tag(tag, source)
    notes, title = release_notes(version, source), "Respawned " + version
    existing = release_view(tag)
    if existing and existing["draft"]:
        require(existing["body"].strip() == notes.strip() and existing["name"] == title,
                "An unrelated draft release already uses this tag")
    with tempfile.TemporaryDirectory(prefix="respawned-github-release-") as temporary:
        staging = Path(temporary)
        for name, body in files.items():
            (staging / name).write_bytes(body)
        notes_file = staging / "release-notes.md"
        notes_file.write_text(notes, encoding="utf-8")
        if existing is None:
            gh("release", "create", tag, "--repo", REPOSITORY, "--draft", "--verify-tag",
               "--target", source, "--title", title, "--notes-file", str(notes_file))
            existing = release_view(tag)
            require(existing is not None and existing["draft"], "GitHub did not return the new draft")
        assets = {entry["name"] for entry in existing["assets"]}
        require(len(assets) == len(existing["assets"]) and assets <= files.keys(),
                "Existing release has unexpected or duplicate assets")
        require(existing["draft"] or assets == files.keys(), "Published release is incomplete; do not replace it")
        downloaded = staging / "downloaded"
        downloaded.mkdir()
        for name in sorted(assets):
            gh("release", "download", tag, "--repo", REPOSITORY, "--pattern", name, "--dir", str(downloaded))
            require(matching_asset(name, files[name], read_regular(downloaded, name)),
                    f"Existing release asset differs; refusing replacement: {name}")
        for name in sorted(files.keys() - assets):
            gh("release", "upload", tag, str(staging / name), "--repo", REPOSITORY)
            gh("release", "download", tag, "--repo", REPOSITORY, "--pattern", name, "--dir", str(downloaded))
            require(read_regular(downloaded, name) == files[name], f"Uploaded asset differs: {name}")
        verify_remote_tag(tag, source)
        if existing["draft"]:
            gh("release", "edit", tag, "--repo", REPOSITORY, "--draft=false")
        final = release_view(tag)
        require(final is not None and not final["draft"] and {entry["name"] for entry in final["assets"]} == files.keys(),
                "GitHub did not confirm a complete published release")
        return final["html_url"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "publish"))
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    tag, version, source = validate_source(dict(os.environ), check_merged=args.action == "validate")
    if args.action == "validate":
        print(f"Verified merged source {source} for {tag}")
        return
    require(args.artifacts is not None, "Publishing requires the qualified artifact directory")
    files = qualified_files(args.artifacts.resolve(), version, source)
    print(publish(tag, version, source, files))


if __name__ == "__main__":
    main()
