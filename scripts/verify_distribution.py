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
from datetime import UTC, datetime, timedelta
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
# Keep the probe independent of the caller's model, connection and credentials.
for key in list(os.environ):
    if key.startswith(("RESPAWNED_", "LITELLM_", "OPENAI_")) and key != "RESPAWNED_DISTRIBUTION_POSTGRES_URL":
        os.environ.pop(key)
os.environ["RESPAWNED_REVIEW_TOKEN"] = "distribution-review-test-token"
assert shutil.which("node") is None

import respawned
from respawned.__main__ import DEFAULT_SEED_DIR
from respawned.adapters import load_legacy_seed
from respawned.api.app import app
from respawned.client import RespawnedClient
from respawned.config import DEFAULT_POLICY_PATH
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
assert app.openapi()["info"]["version"] == version("respawned")
assert {"/healthz", "/readyz", "/v1/workflow/import", "/v1/workflow/queue",
        "/v1/workflow/records/{record_id}/draft", "/v1/workflow/drafts/{draft_id}",
        "/v1/process", "/v1/outbox/pending", "/v1/outbox/{outbox_id}/receipt"} <= set(app.openapi()["paths"])

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


def verify_http(prefix, *, environment=None, api_only=False, exercise=None, expected_ready=False):
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
            try:
                with urlopen(base + "/readyz", timeout=10) as response:
                    assert response.status == 200
                    assert json.load(response) == {"status": "ready"}
                    actual_ready = True
            except HTTPError as error:
                assert error.code == 503
                actual_ready = False
            assert actual_ready is expected_ready
            status_environment = dict(environment if environment is not None else os.environ,
                                      DB_HOST="never-connect.invalid", DB_PORT="not-a-client-port",
                                      DB_PASSWORD="distribution-password-sentinel",
                                      RESPAWNED_REVIEW_TOKEN="invalid\nstatus-test-token")
            expected_status_exit = 0 if actual_ready else 1
            report = json.loads(invoke([console], "--api-url", base, "status", "--json", "--timeout", "10",
                                       environment=status_environment, expected=expected_status_exit))
            assert commands[-1]["exit_code"] == expected_status_exit
            assert report["api_url"] == base
            assert report["client_version"] == report["engine_version"] == version("respawned")
            assert report["compatibility"] == "same_major"
            assert report["health"] == {"status": "ok", "detail": None}
            assert report["readiness"]["status"] == ("ready" if actual_ready else "not_ready")
            assert report["workflow_access"] == "not_checked"
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
            for protected in ("/v1/workflow/config", "/v1/inbox", "/v1/outbox/pending"):
                try:
                    urlopen(base + protected, timeout=5)
                    raise AssertionError("Installed engine exposed an unauthenticated data route")
                except HTTPError as error:
                    assert error.code == 401
            if exercise is not None:
                exercise(base)
            return sorted(verified)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def invoke(prefix, *args, environment=None, expected=0, stdin=""):
    completed = subprocess.run(
        [*map(str, prefix), *args], env=environment, capture_output=True,
        text=True, input=stdin, timeout=45, check=False,
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
    help_output = invoke(prefix, "--help")
    assert "demo" in help_output and "status" in help_output
    for command in ("init", "demo", "serve", "ui", "status", "import", "sync", "draft", "review", "inbox", "outbox", "process"):
        invoke(prefix, command, "--help")
    invoke(prefix, "--api-url", "http://127.0.0.1:0", "sync", expected=1)
    unavailable = dict(os.environ, DB_HOST="127.0.0.1", DB_PORT="0",
                       DB_NAME="unavailable", DB_USER="unavailable",
                       DB_PASSWORD="distribution-password-sentinel",
                       PGCONNECT_TIMEOUT="2")
    invoke(prefix, "init", environment=unavailable, expected=1)

# The installed app must serve Setup even when PostgreSQL is unavailable.
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
        def exercise_workflow(base):
            # An invalid client DB configuration proves commands use HTTP.
            client_environment = dict(environment, RESPAWNED_API_URL=base,
                                      DB_HOST="127.0.0.1", DB_PORT="0",
                                      DB_NAME="not-the-engine", DB_USER="not-the-engine",
                                      DB_PASSWORD="distribution-password-sentinel",
                                      NO_COLOR="1")
            client = RespawnedClient(base, token=environment["RESPAWNED_REVIEW_TOKEN"])
            now = datetime.now(UTC)
            payload = {
                "opportunities": [{
                    "id": "distribution:application", "kind": "job_application",
                    "title": "Distribution fixture", "status": "open",
                    "created_at": (now - timedelta(days=8)).isoformat(),
                    "contact_key": "distribution:alex", "contact_name": "Alex",
                    "contact_email": "alex@example.com", "preferred_channel": "email",
                }],
                "activities": [{
                    "id": "distribution:reply", "opportunity_id": "distribution:application",
                    "type": "contact_replied", "occurred_at": (now - timedelta(hours=1)).isoformat(),
                    "direction": "inbound", "channel": "email", "classification": "human",
                }],
            }
            source = Path.cwd() / "records.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            first = json.loads(invoke([console], "import", "--file", str(source), environment=client_environment))
            assert first == {"opportunities_upserted": 1, "activities_inserted": 1}, first
            replay = json.loads(invoke(module, "import", "--file", "-", environment=client_environment,
                                       stdin=json.dumps(payload)))
            assert replay == {"opportunities_upserted": 1, "activities_inserted": 0}, replay
            body = "Hi Alex, thanks for your message. I can share an update tomorrow.\n\nMorgan"
            message = Path.cwd() / "draft.txt"
            message.write_text(body, encoding="utf-8")
            draft = json.loads(invoke([console], "draft", "distribution:application",
                                      "--body-file", str(message), environment=client_environment))
            assert draft["body"] == body and draft["status"] == "pending", draft
            again = json.loads(invoke(module, "draft", "distribution:application",
                                      "--body-file", "-", environment=client_environment, stdin=body))
            assert again["id"] == draft["id"]
            assert client.get_draft(draft["id"])["body"] == body
            queue = client.queue()
            invoke([console], "sync", "--dry-run", environment=client_environment)
            assert client.queue() == queue
            review = invoke(module, "review", "--limit", "1", environment=client_environment, stdin="a\n")
            assert "1 approved" in review, review
            pending = json.loads(invoke([console], "outbox", "--pending", "--json",
                                        environment=client_environment))
            assert len(pending["items"]) == 1, pending
            approved = pending["items"][0]
            assert approved["body"] == body and approved["authorization_mode"] == "human"
            assert approved["contact_address"] == "alex@example.com"
            assert json.loads(invoke(module, "inbox", "--json", environment=client_environment))["total"] == 1
            # A synthetic confirmation verifies receipt behavior; nothing is sent.
            receipt = {"sender": "distribution:simulated", "provider_message_id": "fixture-message",
                       "sent_at": datetime.now(UTC).isoformat()}
            sent = client.record_receipt(approved["id"], receipt)
            assert sent["status"] == "sent" and sent["body"] == body
            assert client.record_receipt(approved["id"], receipt) == sent
            assert client.pending_outbox()["items"] == []
            assert json.loads(invoke(module, "inbox", "--json", environment=client_environment))["total"] == 0
            export = Path.cwd() / "outbox.csv"
            invoke([console], "outbox", "--path", str(export), environment=client_environment)
            with export.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            assert len(rows) == 1 and rows[0]["status"] == "sent" and rows[0]["body"] == body
            fixtures = json.loads(invoke([console], "demo", environment=client_environment))
            assert fixtures == {"opportunities_upserted": 30, "activities_inserted": 82}, fixtures
            repeated = json.loads(invoke(module, "demo", environment=client_environment))
            assert repeated == {"opportunities_upserted": 30, "activities_inserted": 0}, repeated

        assert verify_http([console, "serve"], environment=environment,
                           exercise=exercise_workflow, expected_ready=True) == bundled_http_assets
        with database.connect() as connection:
            counts = connection.execute(text(
                f'SELECT (SELECT count(*) FROM "{schema}".opportunities), '
                f'(SELECT count(*) FROM "{schema}".activities), '
                f'(SELECT count(*) FROM "{schema}".outbox)'
            )).one()
        assert tuple(counts) == (31, 84, 1), counts
        verify_http([console, "serve"], environment=environment, api_only=True, expected_ready=True)
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
    "synthetic_receipt_verified": database_verified,
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
    assert {"examples/outbox_client.py", "docs/agent-prompt.txt"} <= source_files, \
        "Source distribution omitted connector example or agent prompt"
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
        "sdist_includes_outbox_client_and_agent_prompt": True,
        "installed_checks": verified,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Distribution verification passed. Evidence: {output / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
