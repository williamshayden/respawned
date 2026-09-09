import json
import subprocess
from pathlib import Path

REQUIRED_SERVICES = {"app", "db", "litellm", "litellm_db"}
ROOT = Path(__file__).resolve().parents[1]


def _parse_compose_rows(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line]


def test_app_service_receives_litellm_proxy_configuration(compose_environment):
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
        stderr=subprocess.STDOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    app_environment = json.loads(result.stdout)["services"]["app"]["environment"]
    assert app_environment["LITELLM_PROXY_URL"] == "http://litellm:4000"
    assert app_environment["LITELLM_MASTER_KEY"] == "sk-test-master-key"
    assert app_environment["LITELLM_MODEL_ALIAS"] == "respawned-default"


def test_app_startup_creates_required_services(app_stack):
    result = app_stack.run("ps", "-a", "--format", "json")

    assert result.returncode == 0, result.stdout
    services = {row["Service"] for row in _parse_compose_rows(result.stdout)}
    assert services == REQUIRED_SERVICES


def test_app_startup_loads_seed_rows(app_stack):
    result = app_stack.run(
        "exec",
        "-T",
        "db",
        "sh",
        "-lc",
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At '
        '-c "SELECT (SELECT count(*) FROM quotes), '
        '(SELECT count(*) FROM events);"',
    )

    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == "30|82"


def test_app_loads_from_explicit_container_seed_path(app_stack):
    result = app_stack.run("logs", "app")

    assert result.returncode == 0, result.stdout
    assert "'/seed/quotes.json'" in result.stdout
    assert "'/seed/events.jsonl'" in result.stdout
