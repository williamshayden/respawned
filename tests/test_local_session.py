"""Local launch convenience never grants ambient loopback or cross-site authority."""

from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from respawned.api.app import local_browser_origin_guard
from respawned.api.session import BOOTSTRAP_SECONDS, SESSION_SECONDS, LocalSession, create_session_router
from respawned.api.ui import require_review_authorization

ORIGIN = "http://127.0.0.1:8123"
HEADERS = {"Origin": ORIGIN, "X-Respawned-Request": "1"}


@pytest.fixture
def local_client(monkeypatch):
    monkeypatch.delenv("RESPAWNED_REVIEW_TOKEN", raising=False)
    clock = SimpleNamespace(now=100.0)
    manager = LocalSession(8123, clock=lambda: clock.now)
    app = FastAPI()
    app.state.local_session = manager
    app.middleware("http")(local_browser_origin_guard)
    app.include_router(create_session_router())

    @app.api_route("/v1/ui/probe", methods=["GET", "POST", "PUT", "DELETE"], dependencies=[Depends(require_review_authorization)])
    def protected():
        return {"ok": True}

    with TestClient(app, base_url=ORIGIN) as client:
        yield SimpleNamespace(client=client, manager=manager, clock=clock, app=app)


def login(state):
    return state.client.post("/v1/ui/session", json={"secret": state.manager.launch_secret}, headers=HEADERS)


def test_one_use_launch_cookie_reload_and_logout(local_client):
    state = local_client
    secret = state.manager.launch_secret
    assert state.client.get("/v1/ui/probe").status_code == 401
    assert state.client.get("/v1/ui/session").json() == {"authenticated": False, "local_launcher": True}
    response = login(state)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    for field in ("HttpOnly", "SameSite=strict", "Path=/v1/ui", f"Max-Age={SESSION_SECONDS}"):
        assert field in cookie
    assert "Domain=" not in cookie
    assert secret not in cookie
    assert state.client.get("/v1/ui/probe").status_code == 200
    assert state.client.get("/v1/ui/session").json()["authenticated"] is True
    assert state.client.post("/v1/ui/session", json={"secret": secret}, headers=HEADERS).status_code == 401
    for method in ("POST", "PUT", "DELETE"):
        assert state.client.request(method, "/v1/ui/probe", headers=HEADERS).status_code == 200
    previous_cookie = state.client.cookies.get(state.manager.cookie_name)
    assert state.client.delete("/v1/ui/session", headers=HEADERS).status_code == 204
    assert state.client.cookies.get(state.manager.cookie_name) is None
    assert state.client.get("/v1/ui/probe", headers={"Cookie": f"{state.manager.cookie_name}={previous_cookie}"}).status_code == 401


def test_separate_local_server_ports_keep_independent_browser_sessions(local_client):
    first = local_client
    assert login(first).status_code == 200
    second_manager = LocalSession(8124, clock=lambda: first.clock.now)
    second_app = FastAPI()
    second_app.state.local_session = second_manager
    second_app.include_router(create_session_router())
    second_origin = "http://127.0.0.1:8124"
    with TestClient(second_app, base_url=second_origin, cookies=first.client.cookies) as second:
        response = second.post("/v1/ui/session", json={"secret": second_manager.launch_secret},
                               headers={"Origin": second_origin, "X-Respawned-Request": "1"})
        assert response.status_code == 200
        # A browser shares its cookie jar across ports on the same hostname.
        first.client.cookies.update(second.cookies)
        assert first.client.get("/v1/ui/probe").status_code == 200
        assert first.client.delete("/v1/ui/session", headers=HEADERS).status_code == 204
        assert second.get("/v1/ui/session").json()["authenticated"] is True


@pytest.mark.parametrize("headers", [
    {}, {"Origin": ORIGIN}, {"X-Respawned-Request": "1"},
    {**HEADERS, "Origin": "http://attacker.example"},
    {**HEADERS, "Origin": "null"}, {**HEADERS, "Host": "attacker.example:8123"},
    {**HEADERS, "Host": "localhost:8123"},
])
def test_launch_and_cookie_mutations_require_exact_origin_and_header(local_client, headers):
    state = local_client
    denied = state.client.post("/v1/ui/session", json={"secret": state.manager.launch_secret}, headers=headers)
    assert denied.status_code == 403
    assert login(state).status_code == 200  # Bad origin did not consume the secret.
    for method in ("POST", "PUT", "DELETE"):
        assert state.client.request(method, "/v1/ui/probe", headers=headers).status_code == 403


def test_session_reads_reject_rebinding_and_cross_origin(local_client):
    assert login(local_client).status_code == 200
    for headers in ({"Host": "evil.test:8123"}, {"Origin": "http://localhost:8123"},
                    {"Host": "evil.test:8123", "X-Forwarded-Host": "127.0.0.1:8123"}):
        assert local_client.client.get("/v1/ui/probe", headers=headers).status_code == 403
        assert local_client.client.get("/v1/ui/session", headers=headers).status_code == 403


def test_launch_and_session_expiry_are_server_enforced(local_client):
    local_client.clock.now += BOOTSTRAP_SECONDS
    assert login(local_client).status_code == 401
    local_client.app.state.local_session = LocalSession(8123, clock=lambda: local_client.clock.now)
    local_client.manager = local_client.app.state.local_session
    assert login(local_client).status_code == 200
    local_client.clock.now += SESSION_SECONDS
    assert local_client.client.get("/v1/ui/probe").status_code == 401
    assert local_client.client.get("/v1/ui/session").json()["authenticated"] is False


def test_bad_secret_and_ordinary_serve_do_not_enable_sessions(local_client, monkeypatch):
    state = local_client
    assert state.client.post("/v1/ui/session", json={"secret": "wrong-é"}, headers=HEADERS).status_code == 401
    state.app.state.local_session = None
    assert login(state).status_code == 404
    assert state.client.get("/v1/ui/probe").status_code == 404
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "api-secret")
    assert state.client.get("/v1/ui/probe").status_code == 401
    # Bearer is still the explicit script/API mode, with no cookie or CSRF header.
    assert state.client.post("/v1/ui/probe", headers={"Authorization": "Bearer api-secret"}).status_code == 200


def test_launcher_parser_and_remote_bind_refusal(monkeypatch):
    from respawned import __main__ as entrypoint

    calls = []
    monkeypatch.setattr("respawned.cli.browser.launch_ui", lambda port, **kwargs: calls.append((port, kwargs)))
    entrypoint.main(["ui", "--port", "8123", "--no-open"])
    assert calls == [(8123, {"open_browser": False})]
    with pytest.raises(SystemExit):
        entrypoint.main(["ui", "--host", "0.0.0.0"])
    for port in (0, -1, 65536):
        with pytest.raises(ValueError):
            LocalSession(port)


def test_launcher_binds_before_releasing_a_capability(monkeypatch, capsys):
    import socket
    from respawned.cli.browser import launch_ui

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with pytest.raises(OSError):
            launch_ui(occupied.getsockname()[1])
    assert opened == []
    assert "#login=" not in capsys.readouterr().out
