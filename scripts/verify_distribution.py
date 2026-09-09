"""Build and exercise installed release artifacts outside the source checkout.

Run with uv and Python 3.12+ available. Each invocation requires a new output
directory and retains artifacts, installation logs, and a JSON verification
report. Optional database checks use generated schemas, never existing data.
No drafting model or delivery provider is invoked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]


# This probe runs only with an installed wheel's Python, from a directory that
# contains no package sources. Its imports therefore verify the distribution.
INSTALLED_PROBE = r'''
import csv
from html.parser import HTMLParser
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from uuid import uuid4

# No Node executable, frontend checkout, or UI override is available at runtime.
os.environ["PATH"] = str(Path(sys.executable).parent)
os.environ.pop("RESPAWNED_UI_DIST", None)
os.environ["RESPAWNED_REVIEW_TOKEN"] = "distribution-review-test-token"
assert shutil.which("node") is None

import respawned
from respawned.__main__ import DEFAULT_SEED_DIR
from respawned.adapters import load_legacy_seed
from respawned.api.app import app
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.policy import load_policy
from respawned.db.helpers.pg_connect import DEFAULT_SCHEMA_PATH

root = Path(respawned.__file__).resolve().parent
assert root.is_relative_to(Path(sys.prefix).resolve()), root
policy = load_policy(DEFAULT_POLICY_PATH)
assert policy.review.mode == "human"
assert "authorization_mode" in DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8")
assert DEFAULT_SEED_DIR.is_relative_to(root)
batch = load_legacy_seed(DEFAULT_SEED_DIR / "quotes.json", DEFAULT_SEED_DIR / "events.jsonl")
assert len(batch.opportunities) == 30
assert len({row["id"] for row in batch.activities}) == 82
assert {"/healthz", "/readyz", "/v1/ingest", "/v1/inbox", "/v1/process", "/v1/drafts", "/v1/outbox"} <= set(app.openapi()["paths"])

bin_dir = Path(sys.executable).parent
console = bin_dir / ("respawned.exe" if os.name == "nt" else "respawned")
module = [sys.executable, "-m", "respawned"]
commands = []


class AssetLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        key = "src" if tag == "script" else "href" if tag == "link" else None
        if key and values.get(key):
            self.paths.append(values[key])


def verify_http(prefix, *, environment=None, api_only=False):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [*map(str, prefix), "--host", "127.0.0.1", "--port", str(port)]
    if api_only:
        command.append("--api-only")
    with open(Path.cwd() / "http-server.log", "wb") as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=log)
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urlopen(base + "/healthz", timeout=1) as response:
                        assert response.status == 200
                    break
                except (URLError, TimeoutError):
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise AssertionError("Installed server did not become ready; see http-server.log")
                    time.sleep(0.1)
            if api_only:
                try:
                    urlopen(base + "/", timeout=5)
                    raise AssertionError("API-only server unexpectedly served the UI")
                except HTTPError as error:
                    assert error.code == 404
                return []

            with urlopen(base + "/", timeout=5) as response:
                assert response.headers.get_content_type() == "text/html"
                index = response.read().decode("utf-8")
            assert "<title>Respawned</title>" in index
            links = AssetLinks()
            links.feed(index)
            assert any(value.endswith(".js") for value in links.paths)
            assert any(value.endswith(".css") for value in links.paths)
            pending = [*links.paths, "/THIRD_PARTY_NOTICES.txt", "/bundle-manifest.json"]
            verified = []
            while pending:
                path = pending.pop()
                assert path.startswith("/"), path
                if path in verified:
                    continue
                with urlopen(base + path, timeout=5) as response:
                    assert response.status == 200
                    body = response.read()
                    assert body
                    if path.endswith(".js"):
                        assert "javascript" in response.headers.get_content_type()
                    if path.endswith(".css"):
                        assert response.headers.get_content_type() == "text/css"
                        pending.extend(re.findall(r'url\([\"\x27]?(/[^)\"\x27]+)', body.decode("utf-8")))
                verified.append(path)
            assert any(path.endswith(".woff2") for path in verified), verified
            try:
                urlopen(base + "/v1/ui/config", timeout=5)
                raise AssertionError("Bundled UI shadowed a protected API route")
            except HTTPError as error:
                assert error.code == 401
            return sorted(verified)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def invoke(prefix, *args, environment=None, expected=0):
    completed = subprocess.run(
        [*map(str, prefix), *args], env=environment, capture_output=True,
        text=True, timeout=45, check=False,
    )
    output = completed.stdout + completed.stderr
    commands.append({"arguments": args, "interface": "module" if len(prefix) > 1 else "console", "exit_code": completed.returncode})
    if expected == 0:
        assert completed.returncode == 0, output
    else:
        assert completed.returncode != 0, output
    # A deliberately invalid local connection must not disclose its password.
    assert "distribution-password-sentinel" not in output
    return completed.stdout


for prefix in ([console], module):
    assert "respawned " + version("respawned") in invoke(prefix, "--version")
    assert "demo" in invoke(prefix, "--help")
    for command in ("init", "demo", "serve", "sync", "review", "inbox", "outbox"):
        invoke(prefix, command, "--help")
    invoke(prefix, "sync", "--policy", str(Path.cwd() / "missing-policy.yaml"), expected=1)
    unavailable = dict(os.environ, DB_HOST="127.0.0.1", DB_PORT="0",
                       DB_NAME="unavailable", DB_USER="unavailable",
                       DB_PASSWORD="distribution-password-sentinel",
                       PGCONNECT_TIMEOUT="2")
    invoke(prefix, "init", environment=unavailable, expected=1)

# The installed app must serve its browser demo even when PostgreSQL is unavailable.
bundled_http_assets = verify_http([console, "serve"], environment=unavailable)
database_verified = False
cli_http_verified = False
url = os.environ.get("RESPAWNED_DISTRIBUTION_POSTGRES_URL")
if url:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    connection_url = make_url(url)
    assert connection_url.get_backend_name() == "postgresql"
    schema = "respawned_dist_" + uuid4().hex
    database = create_engine(connection_url)
    schema_created = False
    try:
        with database.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        schema_created = True
        environment = dict(os.environ,
            DB_HOST=str(connection_url.query.get("host", connection_url.host or "")),
            DB_PORT=str(connection_url.query.get("port", connection_url.port or 5432)),
            DB_USER=connection_url.username or "",
            DB_PASSWORD=connection_url.password or "",
            DB_NAME=connection_url.database or "",
            PGOPTIONS=f"-csearch_path={schema}",
        )
        invoke([console], "init", environment=environment)
        first = invoke([console], "demo", environment=environment)
        replay = invoke(module, "demo", environment=environment)
        assert "30 demo opportunities and 82 new activities" in first, first
        assert "30 demo opportunities and 0 new activities" in replay, replay
        with database.connect() as connection:
            counts = connection.execute(text(
                f'SELECT (SELECT count(*) FROM "{schema}".opportunities), '
                f'(SELECT count(*) FROM "{schema}".activities), '
                f'(SELECT count(*) FROM "{schema}".outbox)'
            )).one()
        assert tuple(counts) == (30, 82, 0), counts
        invoke([console], "sync", "--dry-run", "--now", "2026-08-20T12:00:00Z", environment=environment)
        inbox = invoke(module, "inbox", "--json", "--now", "2026-08-20T12:00:00Z", environment=environment)
        assert isinstance(json.loads(inbox), dict)
        export = Path.cwd() / "outbox.csv"
        invoke([console], "outbox", "--path", str(export), environment=environment)
        with export.open(newline="", encoding="utf-8") as source:
            rows = csv.DictReader(source)
            assert "authorization_mode" in rows.fieldnames
            assert list(rows) == []
        assert verify_http([console, "serve"], environment=environment) == bundled_http_assets
        verify_http([console, "serve"], environment=environment, api_only=True)
        cli_http_verified = True
        database_verified = True
    finally:
        try:
            if schema_created:
                with database.begin() as connection:
                    connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            database.dispose()

print(json.dumps({
    "version": version("respawned"),
    "installed_package": str(root),
    "bundled_opportunities": len(batch.opportunities),
    "bundled_unique_activities": len({row["id"] for row in batch.activities}),
    "database_verified": database_verified,
    "bundled_http_assets": bundled_http_assets,
    "node_available_at_runtime": shutil.which("node") is not None,
    "cli_http_verified": cli_http_verified,
    "commands": commands,
}, indent=2))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory for artifacts and verification evidence")
    parser.add_argument("--postgres-url", help="Optional PostgreSQL URL; creates and removes only generated schemas")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv must be available on PATH")
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        environment.pop(name, None)
    if args.postgres_url:
        environment["RESPAWNED_DISTRIBUTION_POSTGRES_URL"] = args.postgres_url

    def run(label: str, arguments: list[str | Path], *, cwd: Path = ROOT) -> str:
        completed = subprocess.run(
            list(map(str, arguments)), cwd=cwd, env=environment,
            capture_output=True, text=True, timeout=240, check=False,
        )
        log = output / f"{label}.log"
        log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError(f"{label} failed with exit code {completed.returncode}; see {log}")
        return completed.stdout

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    run("lock", [uv, "lock", "--check"])
    artifact_dir = output / "artifacts"
    run("build", [uv, "build", "--force-pep517", "--out-dir", artifact_dir])
    wheel, = artifact_dir.glob("*.whl")
    sdist, = artifact_dir.glob("*.tar.gz")
    with tarfile.open(sdist) as archive:
        source_files = {name.partition("/")[2] for name in archive.getnames()}
    assert {
        "web/src/App.tsx", "web/package-lock.json", "web/scripts/bundle-ui.mjs",
        "src/respawned/web_assets/index.html", "src/respawned/web_assets/THIRD_PARTY_NOTICES.txt",
    } <= source_files, "Source distribution omitted frontend sources or bundled assets"
    assert not any("node_modules/" in name or name.startswith("web/dist/") for name in source_files)
    rebuilt_dir = output / "from-sdist"
    run("build-sdist", [uv, "build", sdist, "--wheel", "--force-pep517", "--out-dir", rebuilt_dir])
    rebuilt, = rebuilt_dir.glob("*.whl")

    def contents(path: Path) -> dict[str, bytes]:
        with zipfile.ZipFile(path) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    assert contents(wheel) == contents(rebuilt), "Direct and sdist wheel contents differ"
    requirements = output / "requirements.txt"
    run("export-lock", [uv, "export", "--frozen", "--no-dev", "--no-editable",
                        "--no-emit-project", "--format", "requirements-txt", "--output-file", requirements])
    # Artifact evidence may live under exports/ in the checkout. Installation
    # and execution must still be independent of that checkout's directory.
    verified = []
    with tempfile.TemporaryDirectory(prefix="respawned-distribution-") as temporary_directory:
        installation_root = Path(temporary_directory).resolve()
        if installation_root.is_relative_to(ROOT):
            raise RuntimeError("Set the system temporary directory outside the source checkout")
        for label, artifact in (("wheel", wheel), ("sdist-wheel", rebuilt)):
            work = installation_root / label
            work.mkdir()
            venv = work / "venv"
            run(f"{label}-venv", [uv, "venv", "--python", sys.executable, venv])
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run(f"{label}-dependencies", [uv, "pip", "install", "--python", python,
                                         "--require-hashes", "-r", requirements])
            run(f"{label}-install", [uv, "pip", "install", "--python", python, "--no-deps", artifact])
            run(f"{label}-check", [uv, "pip", "check", "--python", python])
            probe = work / "probe.py"
            probe.write_text(INSTALLED_PROBE, encoding="utf-8")
            result = json.loads(run(f"{label}-probe", [python, probe], cwd=work))
            assert result["version"] == metadata["project"]["version"]
            verified.append(result | {"artifact": str(artifact)})
    report = {
        "version": metadata["project"]["version"],
        "installation_root": str(installation_root),
        "artifact_checksums": {
            str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (wheel, sdist, rebuilt)
        },
        "wheel_contents_match_sdist_build": True,
        "sdist_includes_frontend_source_and_bundle": True,
        "installed_checks": verified,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Distribution verification passed. Evidence: {output / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
