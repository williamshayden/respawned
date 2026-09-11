"""Run repeat installs against tiny, owned environments without downloads or pip installs."""

import base64
import csv
import ensurepip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
from types import SimpleNamespace
import venv

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERSION = "9.8.7"
OWNER = "respawned-versioned-installer-v1"
pytestmark = pytest.mark.skipif(os.name != "posix", reason="The shell installer supports POSIX hosts")


def pip_source():
    """Read an available pip package or ensurepip wheel; never install or download it."""
    spec = importlib.util.find_spec("pip")
    if spec is not None and spec.origin:
        return Path(spec.origin).parent.parent
    directories = [Path(ensurepip.__file__).parent / "_bundled"]
    if directory := sysconfig.get_config_var("WHEEL_PKG_DIR"):
        directories.append(Path(directory))
    for directory in directories:
        if wheels := sorted(directory.glob("pip-*.whl")):
            return wheels[-1]
    pytest.skip("Repeat-install validation tests require pip or a local ensurepip wheel")


def snapshot(directory):
    result = {}
    for path in directory.rglob("*"):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink():
            result[name] = ("symlink", os.readlink(path), path.lstat().st_ino)
        elif path.is_file():
            result[name] = ("file", path.read_bytes(), path.stat().st_mode)
        else:
            result[name] = ("directory", path.stat().st_mode)
    return result


@pytest.fixture
def installation(tmp_path):
    prefix = tmp_path / "prefix with spaces"
    root = prefix / "share" / "respawned"
    release = root / VERSION
    venv.EnvBuilder(with_pip=False, symlinks=True).create(release)
    site, = (release / "lib").glob("python*/site-packages")
    (site / "fixture-pip.pth").write_text(str(pip_source()) + "\n")

    package = site / "respawned"
    assets = package / "web_assets"
    (assets / "assets").mkdir(parents=True)
    (package / "__init__.py").write_text('"""Synthetic package for installer validation."""\n')
    (assets / "index.html").write_text('<script src="/assets/app.js"></script>\n')
    (assets / "bundle-manifest.json").write_text('{"schema": 1}\n')
    (assets / "assets" / "app.js").write_text("console.log('fixture UI');\n")
    metadata = site / ("respawned-" + VERSION + ".dist-info")
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: respawned\nVersion: " + VERSION + "\n")
    with (metadata / "RECORD").open("w", newline="") as stream:
        writer = csv.writer(stream)
        for path in sorted(package.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                writer.writerow([path.relative_to(site).as_posix(), "sha256=" + digest, len(data)])

    executable = release / "bin" / "respawned"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' 'respawned " + VERSION + "'\n")
    executable.chmod(0o755)
    (release / "keep.txt").write_text("Preserve this retained environment.\n")
    (root / "installer.json").write_text(json.dumps({"installer": OWNER}) + "\n")
    wheel = "respawned-" + VERSION + "-py3-none-any.whl"
    receipt = release / "respawned-install.json"
    receipt.write_text(json.dumps({"installer": OWNER, "version": VERSION, "wheel": wheel, "sha256": "a" * 64}) + "\n")

    previous = root / "previous" / "bin" / "respawned"
    previous.parent.mkdir(parents=True)
    previous.write_text("Previous installed command; preserve on failure.\n")
    launcher = prefix / "bin" / "respawned"
    launcher.parent.mkdir()
    launcher.symlink_to(previous)
    (prefix / "user-state.json").write_text('{"keep": true}\n')

    installer = tmp_path / "install.sh"
    template = (ROOT / "scripts/downloads/install.sh.in").read_text()
    for name, value in {"VERSION": VERSION, "WHEEL": wheel, "SHA256": "a" * 64,
                        "BASE_URL": "https://example.invalid"}.items():
        template = template.replace("@@" + name + "@@", json.dumps(value))
    installer.write_text(template)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "python3").symlink_to(sys.executable)
    curl_calls = tmp_path / "curl-called"
    curl = tools / "curl"
    curl.write_text('#!/bin/sh\nprintf "unexpected curl call\\n" > "$FIXTURE_CURL_CALLS"\nexit 99\n')
    curl.chmod(0o755)
    env = os.environ.copy()
    env.update(PATH=str(tools) + os.pathsep + os.defpath, FIXTURE_CURL_CALLS=str(curl_calls))

    def repeat():
        result = subprocess.run(["sh", str(installer), "--prefix", str(prefix),
                                 "--test-base-url", "http://127.0.0.1:9"],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert not curl_calls.exists(), result.stdout + result.stderr
        return result

    return SimpleNamespace(prefix=prefix, root=root, release=release, assets=assets, metadata=metadata,
                           executable=executable, launcher=launcher, previous=previous,
                           receipt=receipt, repeat=repeat)


@pytest.mark.parametrize("damage,detail", [
    ("python", "Dependency check failed"),
    ("dependency", "respawned-installer-missing-dependency"),
    ("version", "Command version check failed"),
    ("metadata", "Installed package metadata has the wrong version"),
    ("index.html", "Bundled UI file is missing"),
    ("bundle-manifest.json", "Bundled UI file is missing"),
    ("assets/app.js", "Bundled UI file is missing"),
    ("changed-asset", "Bundled UI file does not match"),
])
def test_broken_repeat_preserves_retained_environment_and_active_launcher(installation, damage, detail):
    state = installation
    if damage == "python":
        (state.release / "bin" / "python").unlink()
    elif damage == "dependency":
        with (state.metadata / "METADATA").open("a") as stream:
            stream.write("Requires-Dist: respawned-installer-missing-dependency==0.0.0\n")
    elif damage == "version":
        state.executable.write_text("#!/bin/sh\nprintf '%s\\n' 'respawned 0.0.0'\n")
    elif damage == "metadata":
        path = state.metadata / "METADATA"
        path.write_text(path.read_text().replace("Version: " + VERSION, "Version: 0.0.0"))
    elif damage == "changed-asset":
        (state.assets / "assets" / "app.js").write_text("damaged UI\n")
    else:
        (state.assets / damage).unlink()
    before = snapshot(state.prefix)

    result = state.repeat()

    assert result.returncode == 1, result.stdout + result.stderr
    assert detail in result.stderr
    assert "current launcher were left unchanged" in result.stderr
    assert "different --prefix" in result.stderr
    assert "already installed" not in result.stdout
    assert snapshot(state.prefix) == before
    assert state.launcher.resolve() == state.previous
    assert not (state.root / ".install-lock").exists()


def test_valid_repeat_reactivates_only_the_validated_launcher_without_download(installation):
    state = installation
    before = snapshot(state.prefix)

    result = state.repeat()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "already installed and passed validation" in result.stdout
    assert state.launcher.resolve() == state.executable
    after = snapshot(state.prefix)
    before.pop("bin/respawned")
    after.pop("bin/respawned")
    assert after == before
