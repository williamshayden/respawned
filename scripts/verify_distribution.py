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
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]


# This probe runs only with an installed wheel's Python, from a directory that
# contains no package sources. Its imports therefore verify the distribution.
INSTALLED_PROBE = r'''
import csv
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

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

database_verified = False
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
        "installed_checks": verified,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Distribution verification passed. Evidence: {output / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
