"""The local launcher reports workflow readiness independently of its access link."""

from importlib.metadata import version
import socket
from threading import Thread

from fastapi import FastAPI, HTTPException, Request
import pytest
import uvicorn

from respawned.api import app as api_module
from respawned.cli.browser import launch_ui


@pytest.mark.parametrize("ready", [False, True])
def test_launcher_checks_readiness_without_credentials_and_opens_ui_even_when_not_ready(monkeypatch, tmp_path, capsys, ready):
    application = FastAPI()
    requests = []
    servers = []
    opened = []
    errors = []

    @application.get("/healthz")
    def health(request: Request):
        requests.append((request.url.path, request.headers.get("authorization")))
        return {"status": "ok", "engine_version": version("respawned")}

    @application.get("/readyz")
    def readiness(request: Request):
        requests.append((request.url.path, request.headers.get("authorization")))
        if not ready:
            raise HTTPException(503, "Database unavailable")
        return {"status": "ready"}

    original_server = uvicorn.Server

    def server(config):
        config.log_level = "error"
        config.access_log = False
        instance = original_server(config)
        servers.append(instance)
        return instance

    def open_browser(url):
        opened.append(url)
        servers[0].should_exit = True
        return True

    monkeypatch.setattr(api_module, "app", application)
    monkeypatch.setattr(uvicorn, "Server", server)
    monkeypatch.setattr("webbrowser.open", open_browser)
    monkeypatch.setenv("RESPAWNED_STATE_DIR", str(tmp_path / "local-state"))
    # Startup diagnostics must not read or send any token environment value.
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "invalid token with spaces")
    monkeypatch.setenv("RESPAWNED_OUTBOX_TOKEN", "invalid connector token")
    monkeypatch.setenv("RESPAWNED_PROCESS_TOKEN", "invalid processing token")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]

    def launch():
        try:
            launch_ui(port)
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=launch, daemon=True)
    thread.start()
    try:
        thread.join(timeout=15)
    finally:
        if servers:
            servers[0].should_exit = True
        thread.join(timeout=5)
    assert not thread.is_alive(), "Owned launcher did not stop"
    assert errors == []
    assert requests == [("/healthz", None), ("/readyz", None)]
    assert len(opened) == 1 and opened[0].startswith(f"http://127.0.0.1:{port}/#login=")
    assert application.state.local_session is None
    assert not list((tmp_path / "local-state").rglob("*.json"))
    output = capsys.readouterr().out
    assert "Local CLI access: connected." in output
    assert "Browser link (one use, valid for 5 minutes)" in output
    if ready:
        assert "Engine readiness: ready (database and policy)." in output
        assert "not ready" not in output
    else:
        assert "Engine readiness: not ready." in output and "Database unavailable" in output
        assert "RESPAWNED_POLICY_PATH" in output
        assert "Engine readiness: ready" not in output
