import json
import subprocess
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from urllib.request import urlopen

import httpx


APP_SERVICES = {"app", "db"}
ROOT = Path(__file__).resolve().parents[1]


def _parse_compose_rows(output: str) -> list[dict]:
    payload = output.strip()
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return [json.loads(line) for line in payload.splitlines() if line]
    if isinstance(decoded, list):
        return decoded
    return [decoded]


def test_app_profile_has_no_seed_or_litellm_dependency(compose_environment):
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "app",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=compose_environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    services = json.loads(result.stdout)["services"]
    app = services["app"]
    assert set(app["depends_on"]) == {"db"}
    assert app["environment"]["RESPAWNED_PROCESS_TOKEN"] == ""
    assert app["environment"]["RESPAWNED_REVIEW_TOKEN"] == "compose-test-operator"
    assert set(json.loads(result.stdout)["services"]) == APP_SERVICES
    assert services["db"]["image"] == "postgres:16-alpine"
    assert services["db"]["volumes"][0]["target"] == "/var/lib/postgresql/data"
    assert all("/seed" not in str(volume) for volume in app.get("volumes", ()))
    assert "serve" in app["command"]


def test_app_profile_starts_only_long_running_api_and_database(app_stack):
    result = app_stack.run("ps", "-a", "--format", "json")

    assert result.returncode == 0, result.stdout
    rows = _parse_compose_rows(result.stdout)
    assert {row["Service"] for row in rows} == APP_SERVICES
    assert all(row["State"] == "running" for row in rows)
    app = next(row for row in rows if row["Service"] == "app")
    assert app["Health"] == "healthy"


def test_app_health_endpoint_is_reachable(app_stack):
    port = app_stack.environment["APP_PORT"]

    with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
        payload = json.load(response)

    assert response.status == 200
    assert payload == {"status": "ok", "engine_version": version("respawned")}


def test_app_readiness_checks_database_and_processing_requires_operator_access(app_stack):
    port = app_stack.environment["APP_PORT"]
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}
        unauthorized = client.post("/v1/process", json={})
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"


def test_app_startup_initializes_an_empty_generic_schema(app_stack):
    result = app_stack.run(
        "exec",
        "-T",
        "db",
        "sh",
        "-lc",
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At '
        '-c "SELECT '
        '(SELECT count(*) FROM opportunities), '
        '(SELECT count(*) FROM activities), '
        '(SELECT count(*) FROM sync_runs), '
        '(SELECT count(*) FROM candidates), '
        '(SELECT count(*) FROM drafts), '
        '(SELECT count(*) FROM outbox);"',
    )

    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == "0|0|0|0|0|0"


def test_container_recreation_preserves_ingestion_and_backup_restores_it(lifecycle_stack):
    """Exercise only a generated Compose project, including failure and recovery."""
    stack = lifecycle_stack
    port, user, database = (stack.environment[name] for name in ("APP_PORT", "DB_USER", "DB_NAME"))
    now = datetime.now(UTC)
    batch = {
        "opportunities": [{"id": "release-probe", "contact_key": "release-contact",
            "contact_name": "Synthetic Customer", "contact_email": "customer@example.com",
            "status": "open", "created_at": (now - timedelta(days=2)).isoformat()}],
        "activities": [{"id": "release-reply", "opportunity_id": "release-probe",
            "type": "contact_replied", "occurred_at": (now - timedelta(hours=1)).isoformat(),
            "direction": "inbound", "channel": "email"}],
    }

    def command(*args):
        result = stack.run(*args)
        assert result.returncode == 0, result.stdout
        return result.stdout.strip()

    def snapshot(name):
        tables = ("opportunities", "activities", "sync_runs", "candidates", "drafts", "outbox")
        fields = ", ".join(f"'{table}', (SELECT json_agg(t ORDER BY id) FROM {table} t)"
                           for table in tables)
        return json.loads(command("exec", "-T", "db", "psql", "-U", user, "-d", name,
                                  "-At", "-c", f"SELECT json_build_object({fields});"))

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False,
                      headers={"Authorization": f"Bearer {stack.environment['RESPAWNED_REVIEW_TOKEN']}"}) as client:
        response = client.post("/v1/ingest", json=batch)
        assert response.status_code == 200, response.text
        assert response.json() == {"opportunities_upserted": 1, "activities_inserted": 1}
        response = client.post("/v1/ingest", json=batch)
        assert response.status_code == 200, response.text
        assert response.json()["activities_inserted"] == 0
        inbox = client.get("/v1/inbox")
        assert inbox.status_code == 200, inbox.text
        assert len(inbox.json()["items"]) == 1
        # Installed application services create a real policy-authorized reservation
        # with a local stub. No processing action or external provider runs.
        processed = command("exec", "-T", "app", "uv", "run", "--no-sync", "python", "-c", """
import json
from dataclasses import replace
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.policy import ReviewPolicy, load_policy
from respawned.core.workflow import process_candidates
from respawned.db.helpers.pg_connect import get_engine
from respawned.llm.adapter import LiteLLMAdapter

def complete(**_request):
    return {'choices': [{'message': {'content': 'Thanks for your message. Let us discuss the next step.\\n\\nFollow-up Team'}}]}

engine = get_engine()
try:
    result = process_candidates(engine,
        policy=replace(load_policy(DEFAULT_POLICY_PATH), review=ReviewPolicy('automatic')),
        adapter=LiteLLMAdapter(proxy_url='http://unused.invalid', master_key='unused',
            model_alias='simulation', completion_fn=complete))
    print(json.dumps([item.status for item in result.items]))
finally:
    engine.dispose()
""")
        assert json.loads(processed) == ["authorized"]
        original = snapshot(database)
        assert original["opportunities"][0]["contact_email"] == "customer@example.com"
        assert original["activities"][0]["id"] == "release-reply"
        assert len(original["drafts"]) == len(original["outbox"]) == 1
        draft, reservation = original["drafts"][0], original["outbox"][0]
        assert draft["status"] == "approved" and draft["reviewed_at"] is None
        assert reservation["authorization_mode"] == "automatic"
        assert reservation["body"] == draft["body"]
        assert reservation["draft_id"] == draft["id"]
        assert reservation["status"] == "pending" and reservation["sent_at"] is None

        # Readiness must react to DB loss while liveness remains available.
        command("stop", "db")
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503
        command("up", "-d", "--wait", "--wait-timeout", "120", "db")
        assert client.get("/readyz").status_code == 200

        # down without --volumes recreates containers but retains the named data.
        command("down")
        command("up", "-d", "--wait", "--wait-timeout", "120", "app")
        assert snapshot(database) == original
        assert client.get("/readyz").status_code == 200
        assert len(client.get("/v1/inbox").json()["items"]) == 1

        # Restore into a separate fresh database within this disposable project.
        backup, restored = "/tmp/follow-up-release-backup.dump", "follow_up_restore_probe"
        command("exec", "-T", "db", "pg_dump", "-U", user, "-d", database, "-Fc", "-f", backup)
        command("exec", "-T", "db", "createdb", "-U", user, restored)
        command("exec", "-T", "db", "pg_restore", "-U", user, "-d", restored,
                "--exit-on-error", "--single-transaction", backup)
        assert snapshot(restored) == original
