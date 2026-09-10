"""Setup changes are durable, authenticated, and inert until explicit drafting."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from respawned.api import app as api_module, setup
from respawned.core.settings import ModelSettings, configured_adapter, load_model_settings


TOKEN = "setup-review-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
MODEL = {
    "base_url": "http://127.0.0.1:11434/v1", "model_alias": "local-model",
    "timeout_seconds": 12.5, "api_key_env": "RESPAWNED_MODEL_API_KEY",
}


@pytest.fixture
def dependencies(monkeypatch):
    previous = dict(api_module.app.dependency_overrides)
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", TOKEN)
    try:
        yield
    finally:
        api_module.app.dependency_overrides.clear()
        api_module.app.dependency_overrides.update(previous)


@pytest.fixture
def setup_api(postgres_connection, dependencies, monkeypatch):
    @contextmanager
    def begin():
        with postgres_connection.begin_nested():
            yield postgres_connection

    api_module.app.dependency_overrides[api_module.get_api_engine] = lambda: SimpleNamespace(begin=begin)
    monkeypatch.setattr(setup, "probe_setup_database", lambda: load_model_settings(postgres_connection))
    with TestClient(api_module.app, raise_server_exceptions=False) as client:
        yield client, postgres_connection


def test_bootstrap_only_reports_review_enabled_without_resolving_dependencies(dependencies, monkeypatch):
    def forbidden():
        pytest.fail("bootstrap resolved a protected dependency")

    monkeypatch.setattr(setup, "probe_setup_database", forbidden)
    api_module.app.dependency_overrides[api_module.get_api_engine] = forbidden
    monkeypatch.setenv("LITELLM_MASTER_KEY", "private-provider-secret")
    with TestClient(api_module.app) as client:
        assert client.get("/v1/setup/bootstrap").json() == {"review_enabled": True, "workflow_api_prefix": "/v1/workflow"}
        monkeypatch.delenv("RESPAWNED_REVIEW_TOKEN")
        assert client.get("/v1/setup/bootstrap").json() == {"review_enabled": False, "workflow_api_prefix": "/v1/workflow"}


@pytest.mark.parametrize("method,path,payload", [
    ("get", "/setup", None), ("put", "/setup/model", MODEL),
    ("post", "/import", {"opportunities": [{"id": "one"}]}),
    ("get", "/outbox/export", None),
])
def test_setup_requires_review_authorization_before_database(dependencies, monkeypatch, method, path, payload):
    def forbidden():
        pytest.fail("setup resolved database before authorization")

    api_module.app.dependency_overrides[api_module.get_connection] = forbidden
    monkeypatch.setattr(setup, "probe_setup_database", forbidden)
    with TestClient(api_module.app) as client:
        assert client.request(method, "/v1/ui" + path, json=payload).status_code == 401
        monkeypatch.delenv("RESPAWNED_REVIEW_TOKEN")
        assert client.request(method, "/v1/ui" + path, json=payload, headers=HEADERS).status_code == 404


def test_setup_remains_available_with_database_down_and_hides_diagnostics(dependencies, monkeypatch):
    def unavailable():
        raise OperationalError("private SQL", {"password": "private-db-secret"}, RuntimeError("offline"))

    monkeypatch.setattr(setup, "probe_setup_database", unavailable)
    monkeypatch.setenv("LITELLM_MASTER_KEY", "private-provider-secret")
    monkeypatch.setenv("LITELLM_PROXY_URL", "http://localhost:4000")
    with TestClient(api_module.app) as client:
        response = client.get("/v1/ui/setup", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["database"]["status"] == "unavailable"
    assert body["model"]["key_configured"] is True
    assert body["model"]["verified"] is False
    assert body["outbox"]["automatic_delivery"] is False
    assert "private" not in response.text


def test_invalid_environment_is_not_reflected_to_browser(dependencies, monkeypatch):
    monkeypatch.setattr(setup, "probe_setup_database", lambda: None)
    monkeypatch.setenv("LITELLM_PROXY_URL", "http://private-user:private-password@localhost")
    with TestClient(api_module.app) as client:
        response = client.get("/v1/ui/setup", headers=HEADERS)
    assert response.json()["model"]["ready"] is False
    assert response.json()["model"]["base_url"] == ""
    assert "private" not in response.text


@pytest.mark.parametrize("change", [
    {"base_url": "file:///tmp/model"}, {"base_url": "http://user:secret@localhost/v1"},
    {"base_url": "https://example.com/v1?api_key=secret"},
    {"base_url": "https://example.com/#fragment"}, {"base_url": "http://localhost:99999"},
    {"base_url": "http://local\\host/v1"}, {"base_url": "http://localhost\n/v1"},
    {"api_key_env": "DB_PASSWORD"}, {"api_key_env": "RESPAWNED_REVIEW_TOKEN"},
    {"api_key": "raw-browser-secret"}, {"timeout_seconds": 0},
    {"timeout_seconds": True}, {"timeout_seconds": 301}, {"model_alias": " "},
])
def test_model_settings_reject_unsafe_or_unbounded_configuration(change):
    with pytest.raises(ValueError):
        ModelSettings.model_validate({**MODEL, **change})


def test_saved_model_configuration_is_shared_and_never_returns_secrets(setup_api, monkeypatch):
    client, connection = setup_api
    monkeypatch.setenv("LITELLM_MASTER_KEY", "private-legacy-key")
    monkeypatch.setenv("RESPAWNED_MODEL_API_KEY", "private-selected-key")
    response = client.put("/v1/ui/setup/model", headers=HEADERS, json=MODEL)
    assert response.status_code == 200, response.text
    assert response.json() == {**MODEL, "backend": "openai_compatible", "source": "saved", "key_configured": True,
                               "ready": True, "verified": False, "error": None}
    assert "private" not in response.text
    status = client.get("/v1/ui/setup", headers=HEADERS).json()
    assert status["model"] == response.json()
    saved = connection.execute(text("SELECT value FROM application_settings WHERE key = 'drafting_model'")).scalar_one()
    assert saved == {**MODEL, "backend": "openai_compatible"}
    adapter = configured_adapter(connection)
    assert adapter.proxy_url == MODEL["base_url"] and adapter.model_alias == MODEL["model_alias"]
    assert adapter.timeout_seconds == MODEL["timeout_seconds"]
    assert adapter.master_key == "private-selected-key"
    assert "private-selected-key" not in repr(adapter)
    captured = []
    adapter = replace(adapter, completion_fn=lambda **kwargs: (captured.append(kwargs) or
                        {"choices": [{"message": {"content": "Configured copy"}}]}))
    assert adapter.complete([{"role": "user", "content": "Draft"}]) == "Configured copy"
    assert captured[0]["base_url"] == MODEL["base_url"]
    assert captured[0]["api_key"] == "private-selected-key"
    monkeypatch.delenv("RESPAWNED_MODEL_API_KEY")
    assert client.get("/v1/ui/setup", headers=HEADERS).json()["model"]["ready"] is False
    with pytest.raises(ValueError):
        configured_adapter(connection)


def test_command_backend_setup_saves_configuration_without_constructing_or_probing_runtime(setup_api, monkeypatch):
    from respawned.llm import codex

    client, _connection = setup_api
    monkeypatch.setenv("RESPAWNED_CODEX_BIN", "/missing/optional-command")
    monkeypatch.setenv("RESPAWNED_CODEX_SCRATCH_DIR", "/missing/optional-scratch")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Setup must not construct or execute an optional model backend")

    monkeypatch.setattr(codex, "CodexRunner", forbidden)
    monkeypatch.setattr(codex.subprocess, "run", forbidden)
    response = client.put("/v1/ui/setup/model", headers=HEADERS, json={"backend": "codex_cli"})
    assert response.status_code == 200, response.text
    status = response.json()
    assert status["ready"] is True and status["verified"] is False
    assert status["key_configured"] is False and status["error"] is None
    assert "login_ready" not in status
    assert client.get("/v1/ui/setup", headers=HEADERS).json()["model"] == status


def test_http_drafting_resolves_saved_settings_only_at_generation(setup_api, monkeypatch):
    from respawned.core import settings

    client, connection = setup_api
    now = datetime(2026, 9, 9, 12, tzinfo=UTC)
    monkeypatch.setenv("RESPAWNED_MODEL_API_KEY", "local-placeholder")
    assert client.put("/v1/workflow/setup/model", headers=HEADERS, json=MODEL).status_code == 200
    payload = {"opportunities": [{
        "id": "saved-model", "contact_key": "saved-model", "contact_name": "Avery",
        "contact_email": "avery@example.com", "status": "open", "created_at": "2026-09-04T12:00:00Z",
    }], "activities": [{
        "id": "saved-model:reply", "opportunity_id": "saved-model", "type": "contact_replied",
        "direction": "inbound", "channel": "email", "occurred_at": "2026-09-09T11:00:00Z",
    }]}
    assert client.post("/v1/workflow/import", headers=HEADERS, json=payload).status_code == 200
    api_module.app.dependency_overrides[api_module.get_workflow_clock] = lambda: lambda: now

    @contextmanager
    def connect():
        yield connection

    engine = SimpleNamespace(connect=connect, dispose=lambda: None)
    with monkeypatch.context() as runtime_patch:
        runtime_patch.setattr(api_module, "get_api_engine", lambda: engine)
        resolutions, completions = [], []
        updated_model = {**MODEL, "model_alias": "generation-time-model"}

        def resolve_at_generation(current_connection):
            resolutions.append(current_connection)
            adapter = configured_adapter(current_connection)
            return replace(adapter, completion_fn=lambda **kwargs: (
                completions.append(kwargs) or {"choices": [{"message": {"content": "Hi Avery, when would you like to speak?"}}]}
            ))

        monkeypatch.setattr(settings, "configured_adapter", resolve_at_generation)
        assert client.get("/v1/workflow/records", headers=HEADERS).status_code == 200
        assert resolutions == completions == []
        assert client.put("/v1/workflow/setup/model", headers=HEADERS, json=updated_model).status_code == 200
        draft = client.post("/v1/workflow/records/saved-model/draft", headers=HEADERS)
        assert draft.status_code == 200, draft.text
        assert resolutions == [connection] and len(completions) == 1
        assert completions[0]["model"] == updated_model["model_alias"]
        assert completions[0]["base_url"] == MODEL["base_url"]
        assert completions[0]["api_key"] == "local-placeholder"
        monkeypatch.delenv("RESPAWNED_MODEL_API_KEY")
        assert client.post("/v1/workflow/records/saved-model/draft", headers=HEADERS).json() == draft.json()
        assert len(resolutions) == len(completions) == 1


def test_environment_fallback_preserves_existing_cli_configuration(setup_api, monkeypatch):
    _client, connection = setup_api
    monkeypatch.setenv("LITELLM_PROXY_URL", "http://legacy.local:4000")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "legacy-placeholder")
    monkeypatch.setenv("LITELLM_MODEL_ALIAS", "legacy-model")
    adapter = configured_adapter(connection)
    assert adapter.proxy_url == "http://legacy.local:4000"
    assert adapter.model_alias == "legacy-model"


def test_import_uses_canonical_records_and_conflicts_without_processing(setup_api):
    client, connection = setup_api
    payload = {"opportunities": [{"id": "imported-one", "status": "open",
                "created_at": "2026-09-09T12:00:00Z", "kind": "community"}],
               "activities": [{"id": "imported-note", "opportunity_id": "imported-one",
                "type": "note", "occurred_at": "2026-09-09T12:00:00Z"}]}
    response = client.post("/v1/ui/import", headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == {"opportunities_upserted": 1, "activities_inserted": 1}
    assert client.post("/v1/ui/import", headers=HEADERS, json=payload).json()["activities_inserted"] == 0
    payload["activities"][0]["type"] = "other-note"
    assert client.post("/v1/ui/import", headers=HEADERS, json=payload).status_code == 409
    for table in ("sync_runs", "candidates", "drafts", "outbox"):
        assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    exported = client.get("/v1/ui/outbox/export", headers=HEADERS)
    assert exported.json() == {"items": []}
    assert exported.headers["content-disposition"] == 'attachment; filename="respawned-outbox.json"'


def test_import_is_bounded_without_changing_legacy_contract():
    record = {"id": "one", "status": "open", "created_at": "2026-09-09T12:00:00Z"}
    with pytest.raises(ValueError, match="1000"):
        setup.UIImportRequest(opportunities=[record] * 1001)
    with pytest.raises(ValueError, match="2 MB"):
        setup.UIImportRequest(opportunities=[{**record, "id": "x" * 2_000_001}])


def test_export_contains_all_rows_and_never_marks_delivery(dependencies):
    rows = [{
        "id": index, "draft_id": UUID(int=index), "contact_key": f"contact:{index}",
        "contact_address": f"person-{index}@example.com", "contact_name": None,
        "channel": "email", "opportunity_ids": [f"record:{index}"],
        "body": "Reviewed copy", "status": "pending", "authorization_mode": "human",
        "created_at": datetime(2026, 9, 9, 12, tzinfo=UTC), "sent_at": None,
    } for index in range(1, 206)]
    statements = []

    def execute(statement):
        statements.append(str(statement))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    @contextmanager
    def begin():
        yield SimpleNamespace(execute=execute)

    api_module.app.dependency_overrides[api_module.get_api_engine] = lambda: SimpleNamespace(begin=begin)
    with TestClient(api_module.app) as client:
        response = client.get("/v1/ui/outbox/export", headers=HEADERS)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 205 and items[-1]["id"] == 205
    assert all(item["status"] == "pending" and item["sent_at"] is None for item in items)
    assert len(statements) == 1 and statements[0].strip().startswith("SELECT")
    assert "LIMIT" not in statements[0]


def test_setup_probe_has_bounded_waits_and_disposes_connections(monkeypatch):
    captured = {}

    @contextmanager
    def connect():
        raise OperationalError("private SQL", {}, RuntimeError("offline"))
        yield  # pragma: no cover

    def create(_url, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(connect=connect, dispose=lambda: captured.update(disposed=True))

    monkeypatch.setattr(setup, "create_engine", create)
    with pytest.raises(OperationalError):
        setup.probe_setup_database()
    assert captured["connect_args"]["connect_timeout"] == 3
    assert "statement_timeout=2000" in captured["connect_args"]["options"]
    assert "lock_timeout=2000" in captured["connect_args"]["options"]
    assert captured["disposed"] is True


@pytest.mark.parametrize("path,payload", [("/setup/model", MODEL), ("/import", {
    "opportunities": [{"id": "one", "status": "open", "created_at": "2026-09-09T12:00:00Z"}],
})])
def test_setup_writes_do_not_report_success_when_commit_fails(dependencies, monkeypatch, path, payload):
    @contextmanager
    def begin():
        yield object()
        raise SQLAlchemyError("private commit diagnostic")

    api_module.app.dependency_overrides[api_module.get_api_engine] = lambda: SimpleNamespace(begin=begin)
    monkeypatch.setattr(setup, "save_model_settings", lambda *_args: None)
    monkeypatch.setattr(setup, "ingest_records", lambda *_args, **_kwargs:
                        SimpleNamespace(opportunities_upserted=1, activities_inserted=0))
    with TestClient(api_module.app, raise_server_exceptions=False) as client:
        response = client.request("PUT" if path.endswith("model") else "POST",
                                  "/v1/ui" + path, headers=HEADERS, json=payload)
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
