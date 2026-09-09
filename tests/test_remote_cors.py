"""Remote browser origins are opt-in and retain reviewer authorization."""

import os
import socket
import subprocess
import sys

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from respawned.api.app import app, local_browser_origin_guard
from respawned.api.cors import RemoteUIMiddleware, parse_ui_origins
from respawned.api.session import LocalSession
from respawned.api.ui import require_review_authorization


ORIGINS = ["http://127.0.0.1:8000", "https://dashboard.example.com"]


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "remote-test-token")
    monkeypatch.setenv("RESPAWNED_UI_ORIGINS", ",".join(ORIGINS))
    application = FastAPI()
    application.add_middleware(RemoteUIMiddleware)
    application.middleware("http")(local_browser_origin_guard)
    calls = []

    def database():
        calls.append("database")

    @application.api_route("/v1/ui/probe", methods=["GET", "POST", "PUT", "DELETE"],
                           dependencies=[Depends(require_review_authorization), Depends(database)])
    def probe():
        return {"ok": True}

    with TestClient(application, base_url="http://127.0.0.1:8123") as client:
        yield application, client, calls


def preflight(client, origin, *, method="GET", headers="authorization"):
    return client.options("/v1/ui/probe", headers={
        "Origin": origin, "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers,
    })


def test_real_api_registers_remote_middleware():
    assert any(middleware.cls is RemoteUIMiddleware for middleware in app.user_middleware)


@pytest.mark.parametrize("command", ["serve", "ui"])
def test_invalid_origins_stop_cli_startup_before_serving_or_releasing_local_access(command):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    result = subprocess.run(
        [sys.executable, "-m", "respawned", command, "--port", str(port),
         *(["--no-open"] if command == "ui" else [])],
        env={**os.environ, "RESPAWNED_UI_ORIGINS": "*"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "RESPAWNED_UI_ORIGINS must contain" in output
    assert "Application startup complete" not in output
    assert "Uvicorn running" not in output
    assert "#login=" not in output
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.parametrize("origin", ORIGINS)
def test_allowed_origins_still_require_bearer_before_database(remote, origin):
    _application, client, calls = remote
    response = preflight(client, origin)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers
    assert calls == []
    for headers in ({"Origin": origin}, {"Origin": origin, "Authorization": "Bearer wrong"}):
        response = client.get("/v1/ui/probe", headers=headers)
        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == origin
    assert calls == []
    for method in ("GET", "POST", "PUT", "DELETE"):
        assert preflight(client, origin, method=method, headers="authorization,content-type,x-respawned-request").status_code == 200
        response = client.request(method, "/v1/ui/probe", headers={
            "Origin": origin, "Authorization": "Bearer remote-test-token",
        })
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in response.headers
        assert response.headers["cache-control"] == "no-store"
    assert calls == ["database"] * 4


@pytest.mark.parametrize("origin", ["https://attacker.example", "null", "https://dashboard.example.com.attacker.example"])
def test_unlisted_browser_origins_have_no_cors_permission(remote, origin):
    _application, client, calls = remote
    response = preflight(client, origin)
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    assert client.get("/v1/ui/probe", headers={"Origin": origin}).status_code == 401
    assert calls == []


def test_disabled_cors_and_credentials_never_enable_local_cross_origin(remote, monkeypatch):
    application, client, calls = remote
    application.state.local_session = LocalSession(8123)
    for origin in ORIGINS:
        assert preflight(client, origin).status_code == 403
        response = client.get("/v1/ui/probe", headers={
            "Origin": origin, "Authorization": "Bearer remote-test-token",
        })
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
    assert calls == []
    monkeypatch.delenv("RESPAWNED_UI_ORIGINS")
    plain = FastAPI()
    plain.add_middleware(RemoteUIMiddleware)
    with TestClient(plain) as default_client:
        response = preflight(default_client, ORIGINS[0])
        assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("value", ["*", "https://*.example.com", "file://local", "null", "https://user:password@example.com",
    "https://example.com/", "https://example.com/path", "https://example.com?query=1", "https://example.com#fragment",
    "https://example.com:99999", "https://example.com:0", "https://", "https://bad host.example", "https://example.com\nother",
    "https://@example.com", "https://example.com\\"])
def test_origin_configuration_rejects_urls_wildcards_and_credentials(value):
    with pytest.raises(ValueError, match="RESPAWNED_UI_ORIGINS"):
        parse_ui_origins(value)


def test_origin_configuration_is_exact_and_deduplicated():
    assert parse_ui_origins(" ,http://127.0.0.1:8000, https://dashboard.example.com,http://127.0.0.1:8000,") == ORIGINS
    assert parse_ui_origins("") == []
    assert parse_ui_origins("http://[::1]:8000") == ["http://[::1]:8000"]
