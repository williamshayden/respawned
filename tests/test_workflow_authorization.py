"""Every business API requires an explicit operator or narrowly scoped credential."""

from fastapi.testclient import TestClient
import pytest

from respawned.api import app as api_module, ui


@pytest.mark.parametrize("method,path", [
    ("POST", "/v1/ingest"), ("GET", "/v1/inbox"),
    ("GET", "/v1/drafts"), ("GET", "/v1/outbox"),
])
def test_legacy_business_routes_require_operator_before_database(monkeypatch, method, path):
    def forbidden():
        pytest.fail("An unauthorized caller resolved a workflow dependency")

    for dependency in (api_module.get_connection, api_module.get_api_engine,
                       api_module.get_workflow_policy, api_module.get_workflow_clock,
                       ui.get_draft_adapter_factory):
        monkeypatch.setitem(api_module.app.dependency_overrides, dependency, forbidden)
    monkeypatch.delenv("RESPAWNED_REVIEW_TOKEN", raising=False)
    monkeypatch.setenv("RESPAWNED_OUTBOX_TOKEN", "outbox-only")
    monkeypatch.setenv("RESPAWNED_PROCESS_TOKEN", "process-only")
    with TestClient(api_module.app) as client:
        assert client.request(method, path).status_code == 404
        monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "operator")
        for credential in (None, "wrong", "outbox-only", "process-only"):
            headers = {} if credential is None else {"Authorization": f"Bearer {credential}"}
            response = client.request(method, path, headers=headers)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
        assert client.get("/healthz").status_code == 200
        assert client.get("/v1/setup/bootstrap").json() == {
            "review_enabled": True, "workflow_api_prefix": "/v1/workflow",
        }
