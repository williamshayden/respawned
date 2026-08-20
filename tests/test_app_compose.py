import json

REQUIRED_SERVICES = {"app", "db", "litellm", "litellm_db"}


def _parse_compose_rows(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line]


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
