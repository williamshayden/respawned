"""Raw HTTP bounds apply before parsing, authorization dependencies, or database work."""

import asyncio
import importlib
import json

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from respawned.api.body_limit import MAX_REQUEST_BYTES, RequestBodyLimitMiddleware
from respawned.api.cors import RemoteUIMiddleware
from respawned.api.session import LocalSession
from respawned.api.ui import require_review_authorization

api = importlib.import_module("respawned.api.app")


def streamed_request(app, chunks, *, path="/v1/workflow/import", headers=(), method="POST"):
    async def request():
        consumed = 0
        received = 0
        sent = []
        done = asyncio.Event()
        async def receive():
            nonlocal consumed, received
            if received < len(chunks):
                body = chunks[received]
                received += 1
                consumed += len(body)
                return {"type": "http.request", "body": body, "more_body": received < len(chunks)}
            await done.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                done.set()
        await asyncio.wait_for(app({"type": "http", "asgi": {"version": "3.0"},
            "http_version": "1.1", "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "server": ("127.0.0.1", 8123), "client": ("127.0.0.1", 19000),
            "headers": [(b"host", b"127.0.0.1:8123"), (b"content-type", b"application/json"), *headers],
        }, receive, send), timeout=5)
        start = next(item for item in sent if item["type"] == "http.response.start")
        body = b"".join(item.get("body", b"") for item in sent)
        return start, json.loads(body), consumed
    return asyncio.run(request())


@pytest.mark.parametrize("path", ["/v1/workflow/import", "/v1/ingest", "/v1/process"])
@pytest.mark.parametrize("declared", [False, True])
def test_engine_rejects_oversize_before_database_or_model(monkeypatch, path, declared):
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "owned-body-test")
    def forbidden():
        pytest.fail("Oversized body reached database or model resolution")
    monkeypatch.setitem(api.app.dependency_overrides, api.get_api_engine, forbidden)
    monkeypatch.setitem(api.app.dependency_overrides, api.get_workflow_adapter, forbidden)
    chunks = [b"x" * 65536] * 48
    headers = [(b"content-length", str(sum(map(len, chunks))).encode())] if declared else []
    start, body, consumed = streamed_request(api.app, chunks, path=path, headers=headers)
    assert start["status"] == 413
    assert "2 MB" in body["detail"]
    assert (b"cache-control", b"no-store") in start["headers"]
    assert consumed == 0 if declared else MAX_REQUEST_BYTES < consumed <= MAX_REQUEST_BYTES + 65536
    assert consumed < sum(map(len, chunks))


@pytest.fixture
def bounded_app(monkeypatch):
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "owned-body-test")
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware)
    app.add_middleware(RemoteUIMiddleware, origins=["https://ui.example.test"])
    app.middleware("http")(api.local_browser_origin_guard)
    @app.post("/v1/echo", dependencies=[Depends(require_review_authorization)])
    def echo(payload: dict):
        return {"characters": len(payload.get("text", ""))}
    return app


def test_limit_accepts_exact_size_and_small_authenticated_requests(bounded_app):
    with TestClient(bounded_app) as client:
        body = b'{"text":"' + b"a" * (MAX_REQUEST_BYTES - len(b'{"text":""}')) + b'"}'
        assert len(body) == MAX_REQUEST_BYTES
        response = client.post("/v1/echo", content=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer owned-body-test"})
        assert response.status_code == 200
        assert response.json()["characters"] == MAX_REQUEST_BYTES - 11
        assert client.post("/v1/echo", json={"text": "tiny"}).status_code == 401
        assert client.post("/v1/echo", json={"text": "tiny"}, headers={
            "Authorization": "Bearer owned-body-test"}).json() == {"characters": 4}


def test_oversize_responses_preserve_remote_cors_and_local_origin_guard(bounded_app):
    headers = [(b"origin", b"https://ui.example.test"),
               (b"content-length", str(MAX_REQUEST_BYTES + 1).encode())]
    start, _body, consumed = streamed_request(bounded_app, [b""], path="/v1/echo", headers=headers)
    assert start["status"] == 413 and consumed == 0
    assert (b"access-control-allow-origin", b"https://ui.example.test") in start["headers"]
    bounded_app.state.local_session = LocalSession(8123)
    start, _body, consumed = streamed_request(bounded_app, [b""], path="/v1/echo", headers=headers)
    assert start["status"] == 403 and consumed == 0
    assert not any(name.startswith(b"access-control-") for name, _ in start["headers"])
    assert (b"cache-control", b"no-store") in start["headers"]


def test_streamed_body_cannot_bypass_limit_with_small_declared_size(bounded_app):
    start, _body, consumed = streamed_request(bounded_app, [b"x" * (MAX_REQUEST_BYTES + 1)],
        path="/v1/echo", headers=[(b"content-length", b"1")])
    assert start["status"] == 413
    assert consumed == MAX_REQUEST_BYTES + 1
