"""Build a website download directory without publishing it.

Python 3.12+ and uv are required. Run from any directory:
    python3 /path/to/respawned/scripts/build_downloads.py --output /path/to/new-directory

The result contains install.sh, downloads/*.whl and *.tar.gz, SHA256SUMS, and
release.json. The installer pins the actual wheel built in this invocation.
Builds always use a git archive of HEAD, excluding local and ignored files.
Normal builds require a clean checkout; --allow-dirty permits testing modified
tooling but still packages committed application content only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, UTC
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/downloads/install.sh.in"
BASE_URL = "https://respawned.williamshayden.com"


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *arguments], text=True).strip()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_bundle(source: Path) -> tuple[dict[str, bytes], str]:
    """Check the source digest written by web/scripts/bundle-ui.mjs."""
    web = source / "web"
    inputs = [web / name for name in ("index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts")]
    for name in ("src", "scripts"):
        for path in (web / name).rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Frontend input cannot be a symlink: {path}")
            if path.is_file():
                inputs.append(path)
    source_hash = hashlib.sha256()
    for path in sorted(inputs, key=lambda item: item.relative_to(web).as_posix()):
        source_hash.update(path.relative_to(web).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    packaged = source / "src/respawned/web_assets"
    manifest = json.loads((packaged / "bundle-manifest.json").read_text())
    if manifest.get("schema") != 1 or manifest.get("source_sha256") != source_hash.hexdigest():
        raise ValueError("Packaged UI source digest is stale. Run npm run bundle in web/ and commit the result.")
    assets = {"respawned/web_assets/" + path.relative_to(packaged).as_posix(): path.read_bytes()
              for path in packaged.rglob("*") if path.is_file()}
    if not {"respawned/web_assets/index.html", "respawned/web_assets/THIRD_PARTY_NOTICES.txt"} <= assets.keys():
        raise ValueError("Packaged UI is incomplete")
    return assets, source_hash.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/website",
                        help="new output directory (default: dist/website); existing paths are never replaced")
    parser.add_argument("--allow-dirty", action="store_true", help="allow a local test build; records source_clean=false")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists():
        parser.error(f"Output already exists; choose a new directory: {output}")
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "dist"):
        parser.error("Use dist/ or an output directory outside the source checkout")
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv must be available on PATH")
    dirty = bool(git("status", "--porcelain"))
    if dirty and not args.allow_dirty:
        parser.error("Source checkout is dirty; commit changes or use --allow-dirty for a local test build")
    commit, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    with tempfile.TemporaryDirectory(prefix="respawned-download-build-") as temporary:
        staging = Path(temporary)
        source_archive = staging / "source.tar"
        subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", "--output", str(source_archive), commit], check=True)
        source = staging / "source"
        source.mkdir()
        with tarfile.open(source_archive) as archive:
            archive.extractall(source, filter="data")
        project = tomllib.loads((source / "pyproject.toml").read_text())
        version = project["project"]["version"]
        requires_python = project["project"]["requires-python"]
        if project["project"]["name"] != "respawned" or requires_python != ">=3.12":
            raise ValueError("Update the installer template when changing the package name or supported Python versions")
        bundled_assets, bundled_source_hash = check_bundle(source)
        output.mkdir(parents=True)
        downloads = output / "downloads"
        result = subprocess.run([uv, "build", "--out-dir", str(downloads)], cwd=source,
                                capture_output=True, text=True, timeout=300)
        (output / "build.log").write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"uv build failed; see {output / 'build.log'}")
    wheel, = downloads.glob("*.whl")
    sdist, = downloads.glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        metadata_path, = (name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        if metadata["Name"] != "respawned" or metadata["Version"] != version:
            raise ValueError("Built wheel metadata does not match pyproject.toml")
        for name, expected in bundled_assets.items():
            if archive.read(name) != expected:
                raise ValueError(f"Wheel omitted or changed a bundled UI asset: {name}")
    with tarfile.open(sdist) as archive:
        prefix = archive.getnames()[0].split("/")[0]
        for name, expected in bundled_assets.items():
            member = archive.extractfile(f"{prefix}/src/{name}")
            if member is None or member.read() != expected:
                raise ValueError(f"Source archive omitted or changed a bundled UI asset: {name}")
    wheel_hash = digest(wheel)
    installer = TEMPLATE.read_text()
    for key, value in {"VERSION": version, "WHEEL": wheel.name, "SHA256": wheel_hash, "BASE_URL": BASE_URL}.items():
        placeholder = "@@" + key + "@@"
        if installer.count(placeholder) != 1:
            raise ValueError(f"Installer template must contain exactly one {placeholder}")
        installer = installer.replace(placeholder, json.dumps(value))
    if re.search(r"@@[A-Z_]+@@", installer):
        raise ValueError("Installer contains an unresolved template placeholder")
    target = output / "install.sh"
    target.write_text(installer, encoding="utf-8", newline="\n")
    target.chmod(0o755)
    artifacts = [{"path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size,
                  "sha256": digest(path), "planned_url": BASE_URL + "/" + path.relative_to(output).as_posix()}
                 for path in (target, wheel, sdist)]
    (output / "SHA256SUMS").write_text("".join(f"{item['sha256']}  {item['path']}\n" for item in artifacts))
    manifest = {
        "name": "respawned", "version": version, "requires_python": requires_python,
        "prepared_at": datetime.now(UTC).isoformat(), "published": False,
        "source_commit": commit, "source_tree": tree, "source_clean": not dirty,
        "build_source": "git archive of source_commit; working-tree modifications and ignored files excluded",
        "bundled_ui_source_sha256": bundled_source_hash, "bundled_ui_files": len(bundled_assets),
        "build_backend": project["build-system"]["requires"], "artifacts": artifacts,
        "validation": {"bundled_ui_identical_in_wheel_and_sdist": True,
                       "installer_execution_verified": False, "public_hosting_verified": False},
    }
    (output / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Prepared Respawned {version} downloads: {output}")
    print(f"Wheel SHA-256: {wheel_hash}")
    print("Nothing was published. Run scripts/downloads/verify_installer.py against this directory before release.")


if __name__ == "__main__":
    main()
