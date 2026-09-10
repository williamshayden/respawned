"""The CLI SDK exercises HTTP without a database, drafting model, or sender."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
from threading import Thread
import time
from types import SimpleNamespace

import pytest

from respawned.client import APIError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, RespawnedClient, TOKEN_ENVIRONMENTS
from respawned.local_connection import publish_local_token

OPERATOR = "sdk-operator-test-token"
OUTBOX = "sdk-outbox-test-token"
PROCESS = "sdk-process-test-token"
CSV = 'id,body\r\n1,"Hello — café"\r\n'


@pytest.fixture(autouse=True)
def client_environment(monkeypatch, tmp_path):
    for name in TOKEN_ENVIRONMENTS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("RESPAWNED_API_URL", raising=False)
    monkeypatch.setenv("RESPAWNED_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture
def api():
    state = SimpleNamespace(requests=[], mode="normal", status=200, detail=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, result, content_type="application/json", length=None, encoding=None):
            raw = result if isinstance(result, bytes) else json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw) if length is None else length))
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = {"method": self.command, "path": self.path, "headers": dict(self.headers), "raw": raw}
            state.requests.append(request)
            if state.mode == "redirect":
                self.send_response(307)
                self.send_header("Location", state.url + "/credential-trap")
                self.end_headers()
                return
            if state.mode == "drop":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if state.mode == "slow-headers":
                try:
                    for byte in b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}":
                        self.connection.sendall(bytes([byte]))
                        time.sleep(0.04)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if state.mode == "slow-body":
                self.send_response(200)
                self.end_headers()
                try:
                    for _ in range(20):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.04)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if state.mode == "too-large":
                return self.respond(200, b"x" * (MAX_RESPONSE_BYTES + 1))
            if state.mode == "truncated":
                return self.respond(200, b'{"ok":true}', length=100)
            if state.mode == "invalid":
                return self.respond(200, b"<html>Not an API response</html>", "text/html")
            if state.mode == "encoding":
                return self.respond(200, b"compressed bytes", encoding="gzip")
            if state.mode == "array":
                return self.respond(200, [])
            if state.mode == "error":
                return self.respond(state.status, {"detail": state.detail})
            if self.path.endswith("format=csv"):
                return self.respond(200, CSV.encode("utf-8"), "text/csv; charset=utf-8")
            return self.respond(200, {"method": self.command, "path": self.path,
                                      "payload": json.loads(raw) if raw else None})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    state.client = RespawnedClient(state.url + "/team/", OPERATOR, OUTBOX, PROCESS)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_workflow_methods_use_canonical_routes_with_exact_payloads(api):
    client = api.client
    version = "displayed-version"
    body = "Hello, café — exact text.\nSecond line."
    payload = {"opportunities": [{"id": "ats/42"}], "activities": []}
    calls = [
        (lambda: client.import_records(payload), "POST", "/v1/workflow/import", payload),
        (lambda: client.sync(), "POST", "/v1/workflow/sync", {"limit": 10, "dry_run": False}),
        (lambda: client.sync(4, True), "POST", "/v1/workflow/sync", {"limit": 4, "dry_run": True}),
        (client.queue, "GET", "/v1/workflow/queue", None),
        (lambda: client.records(workspace_id="scope/one"), "GET", "/v1/workflow/records?limit=50&offset=0&workspace_id=scope%2Fone", None),
        (lambda: client.get_record("ats/42?x#y"), "GET", "/v1/workflow/records/ats%2F42%3Fx%23y", None),
        (lambda: client.draft("ats/42"), "POST", "/v1/workflow/records/ats%2F42/draft", None),
        (lambda: client.draft("ats/42", body), "POST", "/v1/workflow/records/ats%2F42/draft", {"body": body}),
        (lambda: client.draft_candidate("candidate/1"), "POST", "/v1/workflow/candidates/candidate%2F1/draft", None),
        (lambda: client.draft_candidate("candidate/1", body), "POST", "/v1/workflow/candidates/candidate%2F1/draft", {"body": body}),
        (lambda: client.get_draft("draft/1"), "GET", "/v1/workflow/drafts/draft%2F1", None),
        (lambda: client.edit_draft("draft/1", body, version), "POST", "/v1/workflow/drafts/draft%2F1/edit", {"body": body, "review_token": version}),
        (lambda: client.approve_draft("draft/1", version), "POST", "/v1/workflow/drafts/draft%2F1/approve", {"review_token": version}),
        (lambda: client.reject_draft("draft/1", version), "POST", "/v1/workflow/drafts/draft%2F1/reject", {"review_token": version}),
        (lambda: client.inbox(3, "scope/one"), "GET", "/v1/workflow/inbox?limit=3&workspace_id=scope%2Fone", None),
        (lambda: client.export_outbox(workspace_id="scope/one"), "GET", "/v1/workflow/outbox/export?format=json&workspace_id=scope%2Fone", None),
    ]
    for operation, method, path, expected in calls:
        assert operation() == {"method": method, "path": "/team" + path, "payload": expected}
        request = api.requests[-1]
        assert request["headers"]["Authorization"] == f"Bearer {OPERATOR}"
        assert request["headers"]["Accept-Encoding"] == "identity"
        assert ("Content-Type" in request["headers"]) == (expected is not None)
    assert client.api_url == api.url + "/team"
    with pytest.raises(AttributeError):
        client.api_url = "https://other.example"
    assert len(api.requests) == len(calls)


def test_outbox_and_processing_prefer_their_own_credentials(api):
    receipt = {"sender": "mock", "provider_message_id": "mock:42", "sent_at": "2026-09-10T12:00:00Z"}
    api.client.pending_outbox(4)
    api.client.get_outbox(42)
    api.client.record_receipt(42, receipt)
    api.client.process(3)
    assert [(entry["method"], entry["path"], entry["headers"]["Authorization"]) for entry in api.requests] == [
        ("GET", "/team/v1/outbox/pending?limit=4", f"Bearer {OUTBOX}"),
        ("GET", "/team/v1/outbox/42", f"Bearer {OUTBOX}"),
        ("POST", "/team/v1/outbox/42/receipt", f"Bearer {OUTBOX}"),
        ("POST", "/team/v1/process", f"Bearer {PROCESS}"),
    ]
    assert json.loads(api.requests[2]["raw"]) == receipt
    assert json.loads(api.requests[3]["raw"]) == {"limit": 3}
    operator_only = RespawnedClient(api.url, OPERATOR)
    operator_only.pending_outbox()
    operator_only.process()
    assert all(request["headers"]["Authorization"] == f"Bearer {OPERATOR}" for request in api.requests[-2:])


def test_export_csv_returns_the_server_text_without_reformatting(api):
    assert api.client.export_outbox("csv") == CSV
    assert api.requests[0]["headers"]["Accept"] == "text/csv"
    assert api.requests[0]["headers"]["Authorization"] == f"Bearer {OPERATOR}"
    api.mode = "normal"
    with pytest.raises(APIError, match="did not return CSV"):
        api.client.export_outbox("csv", "workspace")


def test_local_discovery_uses_only_exact_origin_and_never_reads_database_settings(api, monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "unused-database-secret")
    monkeypatch.setenv("DB_HOST", "never-connect.invalid")
    with publish_local_token(api.url, OPERATOR):
        for name in TOKEN_ENVIRONMENTS:
            monkeypatch.setenv(name, "")
        monkeypatch.setenv("RESPAWNED_API_URL", api.url)
        client = RespawnedClient.from_env()
        assert client.api_url == api.url
        client.queue()
        assert api.requests[-1]["headers"]["Authorization"] == f"Bearer {OPERATOR}"
        for url in [api.url + "/other", api.url.replace("127.0.0.1", "localhost"), "https://example.com", "http://127.0.0.1:1"]:
            with pytest.raises(APIError, match="Engine access is required"):
                RespawnedClient.from_env(url).queue()
        # Explicit construction never implicitly adopts a local capability.
        with pytest.raises(APIError, match="Engine access is required"):
            RespawnedClient(api.url).queue()
    assert len(api.requests) == 1


@pytest.mark.parametrize("environment,method", [("RESPAWNED_OUTBOX_TOKEN", "pending_outbox"), ("RESPAWNED_PROCESS_TOKEN", "process")])
def test_restricted_environment_never_escalates_through_local_discovery(api, monkeypatch, environment, method):
    with publish_local_token(api.url, OPERATOR):
        monkeypatch.setenv(environment, "restricted-token")
        client = RespawnedClient.from_env(api.url)
        getattr(client, method)()
        assert api.requests[-1]["headers"]["Authorization"] == "Bearer restricted-token"
        with pytest.raises(APIError, match="Engine access is required"):
            client.queue()
    assert len(api.requests) == 1


def test_no_tokens_gives_actionable_errors_before_network(api):
    client = RespawnedClient(api.url)
    for operation, expected in [(client.queue, "respawned ui"), (client.pending_outbox, "RESPAWNED_OUTBOX_TOKEN"), (client.process, "RESPAWNED_PROCESS_TOKEN")]:
        with pytest.raises(APIError, match=expected) as failure:
            operation()
        assert failure.value.status_code is None
        assert failure.value.ambiguous is False
    assert api.requests == []


def test_invalid_local_state_error_does_not_echo_private_contents(api, monkeypatch):
    def invalid(_url):
        raise ValueError("private-secret-from-file")
    monkeypatch.setattr("respawned.client.read_local_token", invalid)
    with pytest.raises(APIError, match="private local connection") as failure:
        RespawnedClient.from_env(api.url)
    assert "private-secret-from-file" not in str(failure.value)
    assert failure.value.__suppress_context__


@pytest.mark.parametrize("status,ambiguous", [(401, False), (403, False), (404, False), (409, False), (422, False), (503, True)])
def test_http_errors_preserve_status_and_sanitize_secrets_without_retry(api, status, ambiguous):
    api.mode = "error"
    api.status = status
    api.detail = f"Rejected {OPERATOR} {OUTBOX} {PROCESS}\n\x1b[31m more detail " + "x" * 500
    with pytest.raises(APIError) as failure:
        api.client.approve_draft("draft", "version")
    error = failure.value
    assert error.status_code == status and error.ambiguous is ambiguous
    assert all(token not in str(error) for token in [OPERATOR, OUTBOX, PROCESS])
    assert "\x1b" not in str(error) and "\n" not in str(error)
    assert len(str(error)) <= 250
    assert len(api.requests) == 1


def test_validation_errors_do_not_print_rejected_input_objects(api):
    api.mode, api.status = "error", 422
    api.detail = [{"msg": "Timestamp needs a timezone", "input": "do-not-echo", "loc": ["body", "sent_at"]}]
    with pytest.raises(APIError, match="Timestamp needs a timezone") as failure:
        api.client.record_receipt(1, {})
    assert "do-not-echo" not in str(failure.value)


def test_redirect_does_not_forward_authority_or_repeat_a_write(api):
    api.mode = "redirect"
    with pytest.raises(APIError, match="redirects are refused") as failure:
        api.client.approve_draft("draft", "version")
    assert failure.value.status_code == 307
    assert len(api.requests) == 1 and "credential-trap" not in api.requests[0]["path"]


@pytest.mark.parametrize("mode", ["drop", "slow-headers", "slow-body"])
def test_mutating_transport_failures_are_bounded_and_ambiguous(api, mode):
    api.mode = mode
    client = RespawnedClient(api.url, OPERATOR, timeout=0.15)
    started = time.monotonic()
    with pytest.raises(APIError) as failure:
        client.sync()
    assert time.monotonic() - started < 1
    assert failure.value.ambiguous is True
    assert len(api.requests) == 1


@pytest.mark.parametrize("mode,message", [("too-large", "exceeds 4 MiB"), ("truncated", "ended early"), ("invalid", "did not return JSON"), ("encoding", "unsupported content encoding"), ("array", "must be a JSON object")])
def test_unusable_responses_keep_write_outcome_unknown(api, mode, message):
    api.mode = mode
    with pytest.raises(APIError, match=message) as failure:
        api.client.sync()
    assert failure.value.ambiguous is True
    assert len(api.requests) == 1


def test_read_timeout_is_not_a_mutation(api):
    api.mode = "slow-body"
    with pytest.raises(APIError) as failure:
        RespawnedClient(api.url, OPERATOR, timeout=0.15).queue()
    assert failure.value.ambiguous is False
    assert len(api.requests) == 1


@pytest.mark.parametrize("url", ["http://example.com", "http://192.168.1.2", "https://user:secret@example.com", "https://example.com?token=secret", "https://example.com#fragment", "https://example.com?", "https://example.com#", "https://example.com/\npath", "http://127.0.0.1:bad", "http://127.0.0.1:0", "https://example.com:0", "file:///tmp/app", "https://example.com\\@other.example"])
def test_invalid_api_urls_fail_before_network(url):
    with pytest.raises(APIError):
        RespawnedClient(url, OPERATOR)


@pytest.mark.parametrize("timeout", [0, -1, 301, True, float("nan"), float("inf"), "30"])
def test_timeout_is_finite_and_bounded(timeout):
    with pytest.raises(APIError, match="Timeout"):
        RespawnedClient("https://example.com", OPERATOR, timeout=timeout)


def test_invalid_headers_bodies_and_ids_fail_before_network(api):
    for token in ["bad token", "bad\ntoken", "badé", "x" * 8193]:
        with pytest.raises(APIError):
            RespawnedClient(api.url, token)
    for payload in [{"bad": float("nan")}, {"bad": object()}, {"body": "x" * MAX_REQUEST_BYTES}]:
        with pytest.raises(APIError) as failure:
            api.client.import_records(payload)
        assert failure.value.ambiguous is False
    for id in [True, None, ""]:
        with pytest.raises(APIError):
            api.client.get_record(id)
    with pytest.raises(APIError, match="too long"):
        api.client.get_record("x" * 8193)
    assert api.requests == []


def test_client_import_does_not_load_database_model_or_server_modules(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    script = "import sys; sys.path.insert(0, " + repr(str(source)) + "); from respawned.client import RespawnedClient; assert not any(name.startswith(('sqlalchemy', 'psycopg2', 'openai', 'fastapi', 'respawned.db', 'respawned.core')) for name in sys.modules)"
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
