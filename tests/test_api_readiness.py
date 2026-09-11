"""Readiness observes current dependencies without exposing their diagnostics."""

from contextlib import contextmanager
from importlib.metadata import version
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from respawned.api import app as api_module
from respawned.client import RespawnedClient
from http_engine import serve_http


@pytest.fixture
def overrides(monkeypatch):
    def replace(dependency, callback):
        monkeypatch.setitem(api_module.app.dependency_overrides, dependency, callback)
    return replace


def test_liveness_does_not_require_database_policy_or_model(overrides):
    for dependency in (
        api_module.get_api_engine, api_module.get_workflow_policy,
        api_module.get_workflow_adapter,
    ):
        overrides(dependency, lambda: pytest.fail("liveness resolved dependency"))
    with TestClient(api_module.app) as client:
        assert client.get("/healthz").json() == {"status": "ok", "engine_version": version("respawned")}
        assert client.get("/openapi.json").json()["info"]["version"] == version("respawned")


def test_status_client_reports_the_running_api_metadata_and_readiness(overrides):
    failing = [False]

    class Connection:
        def exec_driver_sql(self, _statement):
            if failing[0]:
                raise OperationalError("private SQL", {}, RuntimeError("offline"))

    @contextmanager
    def transaction():
        yield Connection()

    overrides(api_module.get_api_engine, lambda: SimpleNamespace(begin=transaction))
    overrides(api_module.get_workflow_policy, lambda: object())
    overrides(api_module.get_workflow_adapter, lambda: pytest.fail("status invoked model"))
    requests = []
    with serve_http(api_module.app, requests) as url:
        client = RespawnedClient(url, timeout=2)
        ready = client.status()
        assert ready["engine_version"] == ready["client_version"] == version("respawned")
        assert ready["health"]["status"] == "ok"
        assert ready["readiness"]["status"] == "ready"
        failing[0] = True
        unavailable = client.status()
        assert unavailable["health"]["status"] == "ok"
        assert unavailable["readiness"] == {"status": "not_ready", "detail": "HTTP 503: Database unavailable"}
    assert [(item["method"], item["path"]) for item in requests] == [
        ("GET", "/healthz"), ("GET", "/readyz"), ("GET", "/healthz"), ("GET", "/readyz"),
    ]


def test_readiness_database_failure_is_503_and_recovers_without_restart(overrides):
    fail = [True]

    class Connection:
        def exec_driver_sql(self, _statement):
            if fail[0]:
                raise OperationalError("private SQL", {"password": "private-password"}, RuntimeError("offline"))

    @contextmanager
    def transaction():
        yield Connection()

    overrides(api_module.get_api_engine, lambda: SimpleNamespace(begin=transaction))
    with TestClient(api_module.app) as client:
        failure = client.get("/readyz")
        assert failure.status_code == 503
        assert failure.json() == {"detail": "Database unavailable"}
        assert client.get("/healthz").status_code == 200
        fail[0] = False
        assert client.get("/readyz").json() == {"status": "ready"}


def test_readiness_initial_connection_failure_is_generic(overrides):
    def unavailable():
        raise api_module.DatabaseConnectionError("private connection string")

    overrides(api_module.get_api_engine, unavailable)
    with TestClient(api_module.app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_invalid_database_configuration_is_unready_without_exposing_values(monkeypatch):
    monkeypatch.setenv("DB_PORT", "private-invalid-port")
    api_module.get_api_engine.cache_clear()
    with TestClient(api_module.app) as client:
        response = client.get("/readyz")
        assert client.get("/healthz").status_code == 200
    assert response.status_code == 503
    assert response.json() == {"detail": "Database configuration is invalid"}


def test_invalid_policy_is_unready_and_does_not_disclose_file_contents(overrides, monkeypatch, tmp_path):
    @contextmanager
    def transaction():
        yield object()

    overrides(api_module.get_api_engine, lambda: SimpleNamespace(begin=transaction))
    policy = tmp_path / "private-policy.yaml"
    policy.write_text("private-token: invalid-policy", encoding="utf-8")
    monkeypatch.setenv("RESPAWNED_POLICY_PATH", str(policy))
    with TestClient(api_module.app) as client:
        response = client.get("/readyz")
        assert client.get("/healthz").status_code == 200
    assert response.status_code == 503
    assert response.json() == {"detail": "Workflow policy configuration is invalid"}


def test_readiness_checks_required_schema_without_model_configuration(
    postgres_connection, overrides, monkeypatch,
):
    # SAVEPOINTs keep failure probes from aborting the fixture's outer transaction.
    @contextmanager
    def transaction():
        with postgres_connection.begin_nested():
            yield postgres_connection

    overrides(api_module.get_api_engine, lambda: SimpleNamespace(begin=transaction))
    overrides(api_module.get_workflow_adapter, lambda: pytest.fail("readiness called model"))
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    with TestClient(api_module.app) as client:
        assert client.get("/readyz").json() == {"status": "ready"}
        with postgres_connection.begin_nested() as altered_schema:
            postgres_connection.execute(text("ALTER TABLE outbox RENAME COLUMN authorization_mode TO temporarily_absent"))
            assert client.get("/readyz").status_code == 503
            altered_schema.rollback()
        assert client.get("/readyz").status_code == 200


def test_database_engine_is_disposed_when_schema_initialization_fails(monkeypatch):
    disposed = []
    engine = SimpleNamespace(dispose=lambda: disposed.append(True))
    monkeypatch.setattr(api_module, "get_engine", lambda: engine)

    def fail(_engine):
        raise RuntimeError("schema unavailable")

    monkeypatch.setattr(api_module, "create_tables", fail)
    api_module.get_api_engine.cache_clear()
    with pytest.raises(RuntimeError, match="schema unavailable"):
        api_module.get_api_engine()
    assert disposed == [True]
    assert api_module.get_api_engine.cache_info().currsize == 0


def test_application_shutdown_disposes_only_its_cached_engine(monkeypatch):
    disposed = []
    engine = SimpleNamespace(dispose=lambda: disposed.append(True))
    monkeypatch.setattr(api_module, "get_engine", lambda: engine)
    monkeypatch.setattr(api_module, "create_tables", lambda _engine: None)
    api_module.get_api_engine.cache_clear()
    with TestClient(api_module.app):
        assert api_module.get_api_engine() is engine
    assert disposed == [True]
    assert api_module.get_api_engine.cache_info().currsize == 0
