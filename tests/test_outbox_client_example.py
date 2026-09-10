"""The downloadable stdlib client speaks HTTP without importing the application."""

import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Thread
import time
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "outbox_client.py"
spec = importlib.util.spec_from_file_location("outbox_client_example", SCRIPT)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)
TOKEN = "example-test-only"
RECEIPT = {"sender": "test:account", "provider_message_id": "message-42",
           "sent_at": "2026-09-10T10:30:00Z"}


@pytest.fixture
def api():
    state = SimpleNamespace(requests=[], receipt=None, mode="normal")
    item = {"id": 42, "draft_id": "e2ab2d78-3b5c-4446-8a1c-2d995a791b78",
            "contact_key": "ats:alex", "contact_address": "alex@example.com",
            "contact_name": "Alex", "channel": "email", "opportunity_ids": ["ats:123"],
            "body": "= Keep this exact.\nHello, Alex — café.",
            "status": "pending", "authorization_mode": "human",
            "created_at": "2026-09-10T10:00:00Z", "sent_at": None}

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, result):
            raw = result if isinstance(result, bytes) else json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
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
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state.requests.append((self.command, self.path, dict(self.headers), body))
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self.respond(401, {"detail": "Outbox authorization required"})
            if state.mode == "redirect":
                self.send_response(307)
                self.send_header("Location", state.url + "/trap")
                self.end_headers()
                return
            if state.mode == "invalid-json":
                return self.respond(200, b"not JSON")
            if state.mode == "oversized":
                return self.respond(200, b" " * (example.MAX_RESPONSE_BYTES + 1))
            if state.mode == "slow":
                self.send_response(200)
                self.end_headers()
                try:
                    for _ in range(20):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.08)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if state.mode == "unavailable":
                return self.respond(503, {"detail": "Database unavailable"})
            result = item | {"status": "sent" if state.receipt else "pending",
                             "sent_at": state.receipt["sent_at"] if state.receipt else None}
            if self.path.startswith("/engine/v1/outbox/pending?"):
                return self.respond(200, {"items": [] if state.receipt else [result], "has_more": False})
            if self.path == "/engine/v1/outbox/42" and self.command == "GET":
                return self.respond(200, result | {"receipt": state.receipt})
            if self.path == "/engine/v1/outbox/42/receipt" and self.command == "POST":
                payload = json.loads(body)
                if payload.get("sent_at") == "invalid":
                    return self.respond(422, {"detail": [{"msg": "Invalid timestamp"}]})
                if state.receipt and payload != {key: state.receipt[key] for key in RECEIPT}:
                    return self.respond(409, {"detail": "A different receipt is already recorded"})
                state.receipt = payload | {"recorded_at": "2026-09-10T10:31:00Z"}
                return self.respond(200, item | {"status": "sent", "sent_at": payload["sent_at"],
                                                "receipt": state.receipt})
            return self.respond(404, {"detail": "Outbox item not found"})

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    state.client = example.OutboxClient(state.url + "/engine/", TOKEN)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pending_get_and_receipt_use_real_http_and_preserve_snapshot(api):
    pending = api.client.pending(limit=2)
    original = pending["items"][0]
    assert pending["has_more"] is False
    assert original["body"] == "= Keep this exact.\nHello, Alex — café."
    assert api.client.get(42)["receipt"] is None
    sent = api.client.receipt(42, RECEIPT)
    assert sent["status"] == "sent" and sent["body"] == original["body"]
    assert sent["contact_address"] == original["contact_address"]
    assert sent["receipt"] == RECEIPT | {"recorded_at": "2026-09-10T10:31:00Z"}
    assert api.client.receipt(42, RECEIPT) == sent
    assert api.client.pending() == {"items": [], "has_more": False}
    assert api.client.get(42) == sent
    assert api.requests[0][:2] == ("GET", "/engine/v1/outbox/pending?limit=2")
    assert all(request[2]["Authorization"] == f"Bearer {TOKEN}" for request in api.requests)
    posted = [request for request in api.requests if request[0] == "POST"]
    assert all(json.loads(request[3]) == RECEIPT for request in posted)


def test_http_errors_are_concise_and_requests_are_never_retried(api):
    bad = example.OutboxClient(api.url + "/engine", "wrong-test-token")
    with pytest.raises(example.OutboxError, match="HTTP 401: Outbox authorization required"):
        bad.pending()
    with pytest.raises(example.OutboxError, match="HTTP 404"):
        api.client.get(999)
    with pytest.raises(example.OutboxError, match="HTTP 422"):
        api.client.receipt(42, RECEIPT | {"sent_at": "invalid"})
    api.client.receipt(42, RECEIPT)
    with pytest.raises(example.OutboxError, match="HTTP 409"):
        api.client.receipt(42, RECEIPT | {"provider_message_id": "other"})
    api.mode = "unavailable"
    with pytest.raises(example.OutboxError, match="HTTP 503"):
        api.client.pending()
    assert len(api.requests) == 6


def test_redirect_never_reaches_target_with_bearer(api):
    api.mode = "redirect"
    with pytest.raises(example.OutboxError, match="redirects are refused"):
        api.client.pending()
    assert len(api.requests) == 1
    assert not any("/trap" in request[1] for request in api.requests)


@pytest.mark.parametrize("mode,message", [("invalid-json", "did not return JSON"),
                                        ("oversized", "exceeds 4 MiB")])
def test_bad_or_unbounded_responses_are_rejected(api, mode, message):
    api.mode = mode
    with pytest.raises(example.OutboxError, match=message):
        api.client.pending()
    assert len(api.requests) == 1


def test_slow_response_cannot_extend_the_read_budget(api):
    api.mode = "slow"
    client = example.OutboxClient(api.url + "/engine", TOKEN, timeout=0.2)
    started = time.monotonic()
    with pytest.raises(example.OutboxError, match="No retry was made"):
        client.pending()
    assert time.monotonic() - started < 1
    assert len(api.requests) == 1


def test_cli_runs_without_checkout_imports_and_prints_json(api, tmp_path):
    copied = tmp_path / "outbox_client.py"
    shutil.copyfile(SCRIPT, copied)
    env = os.environ | {"RESPAWNED_API_URL": api.url + "/engine", "RESPAWNED_OUTBOX_TOKEN": TOKEN,
                       "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1"}

    def run(*args, environment=env):
        return subprocess.run([sys.executable, "-I", str(copied), *args], cwd=tmp_path,
                              env=environment, text=True, capture_output=True, timeout=5)

    pending = run("pending", "--limit", "1")
    assert pending.returncode == 0 and not pending.stderr
    assert json.loads(pending.stdout)["items"][0]["id"] == 42
    file = tmp_path / "receipt.json"
    file.write_text(json.dumps(RECEIPT))
    receipt = run("receipt", "42", str(file))
    assert receipt.returncode == 0 and json.loads(receipt.stdout)["status"] == "sent"
    got = run("get", "42")
    assert got.returncode == 0 and json.loads(got.stdout)["receipt"]["provider_message_id"] == "message-42"
    failed = run("pending", environment=env | {"RESPAWNED_OUTBOX_TOKEN": ""})
    assert failed.returncode == 1 and not failed.stdout and "Traceback" not in failed.stderr
    assert "Set RESPAWNED_OUTBOX_TOKEN" in failed.stderr
    assert len(api.requests) == 3


def test_invalid_inputs_fail_before_network(api):
    for kwargs in ({"base_url": "http://example.com"}, {"base_url": api.url + "?query=yes"},
                   {"base_url": "https://user:secret@example.com"}, {"base_url": api.url + "#fragment"},
                   {"token": "line\nbreak"}, {"timeout": 0}):
        with pytest.raises(example.OutboxError):
            example.OutboxClient(**({"base_url": api.url, "token": TOKEN} | kwargs))
    for operation in (lambda: api.client.pending(201), lambda: api.client.get(True),
                      lambda: api.client.get(9223372036854775808),
                      lambda: api.client.receipt(42, RECEIPT | {"extra": True})):
        with pytest.raises(example.OutboxError):
            operation()
    assert api.requests == []
