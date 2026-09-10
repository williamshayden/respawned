"""Deliver one approved demo message to a local TEST endpoint, then record its receipt.

The endpoint only writes JSON to the new output directory. It never contacts an
email or messaging provider. Run after human approval in the recording engine.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from threading import Lock, Thread
from urllib.parse import urlsplit

from respawned.client import APIError, RespawnedClient

RECORD_ID = "demo:application:northstar-backend"
RECIPIENT = "maya@northstar.example"
SENDER = "test:local-demo"
CLI_COMMAND = "respawned outbox --pending --json"
MAX_BYTES = 16 * 1024


class DemoError(Exception):
    pass


def persist(path: Path, value) -> None:
    """A successful response follows a flushed file and atomic replacement."""
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(4) + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def pending_item(result: dict) -> dict:
    items = result.get("items")
    if result.get("has_more") is not False or not isinstance(items, list) or len(items) != 1:
        raise DemoError("Expected exactly one pending demo message. Approve the Northstar application in the isolated demo engine first.")
    item = items[0]
    if (not isinstance(item, dict) or item.get("opportunity_ids") != [RECORD_ID]
            or type(item.get("id")) is not int or item["id"] < 1
            or item.get("contact_address") != RECIPIENT or item.get("channel") != "email"
            or item.get("status") != "pending" or item.get("authorization_mode") != "human"
            or not isinstance(item.get("body"), str) or not item["body"].strip()
            or not isinstance(item.get("draft_id"), str) or not item["draft_id"]):
        raise DemoError("The pending message does not match the human-approved demo application. No TEST delivery was attempted.")
    return item


def cli_pending(url: str, token: str) -> tuple[str, dict, list[str]]:
    executable = Path(sys.executable).with_name("respawned.exe" if os.name == "nt" else "respawned")
    command = str(executable) if executable.is_file() else shutil.which("respawned")
    if not command:
        raise DemoError("The respawned command is missing. Run this script in the installed app environment, for example with uv run.")
    # The CLI receives only outbox authority. Database settings are unnecessary.
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith(("DB_", "PG", "RESPAWNED_REVIEW_TOKEN", "RESPAWNED_PROCESS_TOKEN"))}
    environment.update(RESPAWNED_API_URL=url, RESPAWNED_OUTBOX_TOKEN=token)
    argv = [command, "outbox", "--pending", "--json"]
    result = subprocess.run(argv, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=45)
    if result.returncode:
        raise DemoError("The outbox CLI did not complete. Check the demo URL, outbox token, and installed app version.")
    if len(result.stdout.encode("utf-8")) > 4 * 1024 * 1024:
        raise DemoError("The outbox CLI returned more data than this one-message demo allows.")
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise DemoError("The outbox CLI did not return JSON.") from None
    if not isinstance(payload, dict):
        raise DemoError("The outbox CLI did not return an object.")
    return result.stdout, payload, argv


@contextmanager
def test_endpoint(directory: Path, expected: dict, message_id: str):
    directory.mkdir(mode=0o700)
    saved_path = directory / f"{expected['outbox_id']}.json"
    idempotency_key = str(expected["outbox_id"])
    events = []
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_args):
            pass

        def respond(self, status, value):
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path != "/deliveries" or self.headers.get("Authorization") is not None:
                return self.respond(400, {"detail": "TEST endpoint accepts only its delivery payload; do not forward engine credentials."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= MAX_BYTES:
                    raise ValueError
                payload = json.loads(self.rfile.read(length))
            except (ValueError, OSError):
                return self.respond(400, {"detail": "Invalid TEST delivery payload"})
            if self.headers.get("Idempotency-Key") != idempotency_key or payload != expected:
                return self.respond(409, {"detail": "TEST idempotency key or approved message changed"})
            with lock:
                replay = saved_path.exists()
                if replay:
                    saved = json.loads(saved_path.read_text(encoding="utf-8"))
                    if saved["payload"] != payload:
                        return self.respond(409, {"detail": "TEST delivery already has different content"})
                else:
                    saved = {"test_delivery": True, "external_delivery": False,
                             "idempotency_key": idempotency_key, "payload": payload,
                             "confirmation": {"test_delivery": True, "accepted": True,
                                 "message_id": message_id, "sent_at": datetime.now(UTC).isoformat()}}
                    persist(saved_path, saved)
                status = 200 if replay else 201
                events.append({"method": "POST", "path": self.path, "idempotency_key": idempotency_key,
                               "payload": payload, "status": status, "replay": replay,
                               "confirmation": saved["confirmation"]})
                return self.respond(status, saved["confirmation"])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    worker.start()
    try:
        yield server.server_port, saved_path, events
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def post_test_delivery(port: int, payload: dict) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("POST", "/deliveries", body=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Idempotency-Key": str(payload["outbox_id"]),
        })
        response = connection.getresponse()
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or response.status not in {200, 201}:
            raise DemoError("The local TEST endpoint did not confirm acceptance.")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("test_delivery") is not True or result.get("accepted") is not True:
            raise DemoError("The local TEST endpoint returned an invalid confirmation.")
        return response.status, result
    finally:
        connection.close()


def run(url: str, output: Path, token: str) -> dict:
    client = RespawnedClient(url, outbox_token=token, timeout=30)
    parsed = urlsplit(client.api_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path:
        raise DemoError("This recording requires a loopback HTTP engine root.")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    trace = {"test_delivery": True, "external_delivery": False, "passed": False,
             "engine_url": client.api_url, "record_id": RECORD_ID,
             "credential_scope": "outbox only", "events": [], "stage": "read_pending"}
    try:
        stdout, cli_before, argv = cli_pending(client.api_url, token)
        item = pending_item(cli_before)
        before = client.pending_outbox()
        if pending_item(before) != item:
            raise DemoError("The pending snapshot changed between CLI and API reads. Review it before retrying.")
        detail_before = client.get_outbox(item["id"])
        if detail_before.get("receipt") is not None or any(detail_before.get(key) != value for key, value in item.items()):
            raise DemoError("The approved outbox snapshot changed. No TEST delivery was attempted.")
        (output / "cli-before.stdout.txt").write_text(stdout, encoding="utf-8", newline="")
        trace["cli_before"] = {"command": CLI_COMMAND, "argv": argv, "stdout_file": "cli-before.stdout.txt", "result": cli_before}
        trace["approved_snapshot"] = detail_before
        trace["events"].extend([
            {"method": "GET", "path": "/v1/outbox/pending?limit=50", "result": before},
            {"method": "GET", "path": f"/v1/outbox/{item['id']}", "result": detail_before},
        ])
        print("$ " + CLI_COMMAND, flush=True)
        print(stdout, end="" if stdout.endswith("\n") else "\n", flush=True)
        payload = {"outbox_id": item["id"], "recipient": item["contact_address"],
                   "channel": item["channel"], "body": item["body"]}
        trace["delivery_payload"] = payload
        message_id = f"test:outbox:{item['id']}:{item['draft_id']}"
        trace["stage"] = "test_delivery"
        with test_endpoint(output / "test-deliveries", payload, message_id) as (port, saved_path, events):
            trace["test_endpoint"] = f"http://127.0.0.1:{port}/deliveries"
            trace["test_requests"] = events
            status, confirmation = post_test_delivery(port, payload)
            if status != 201:
                raise DemoError("Expected one new TEST delivery.")
            trace["confirmation"] = confirmation
            trace["delivery_file"] = str(saved_path.relative_to(output))
            delivery_hash = hashlib.sha256(saved_path.read_bytes()).hexdigest()
            print("TEST delivery accepted by local HTTP endpoint.", flush=True)
            receipt = {"sender": SENDER, "provider_message_id": confirmation["message_id"], "sent_at": confirmation["sent_at"]}
            persist(output / "receipt.json", receipt)
            trace["stage"] = "record_receipt"
            recorded = client.record_receipt(item["id"], receipt)
            trace["events"].append({"method": "POST", "path": f"/v1/outbox/{item['id']}/receipt", "request": receipt, "result": recorded})
            if recorded.get("status") != "sent" or any(recorded.get(key) != value for key, value in item.items() if key not in {"status", "sent_at"}):
                raise DemoError("Respawned did not preserve the approved message when recording the TEST result.")
            if recorded.get("contact_address") != payload["recipient"]:
                raise DemoError("Respawned's recorded recipient did not match the approved snapshot.")
            recorded_receipt = recorded.get("receipt")
            if (not isinstance(recorded_receipt, dict)
                    or recorded_receipt.get("sender") != receipt["sender"]
                    or recorded_receipt.get("provider_message_id") != receipt["provider_message_id"]
                    or datetime.fromisoformat(recorded_receipt.get("sent_at", "")) != datetime.fromisoformat(receipt["sent_at"])):
                raise DemoError("Respawned did not return the exact TEST confirmation.")
            print(f"Recorded TEST receipt through POST /v1/outbox/{item['id']}/receipt.", flush=True)
            trace["stage"] = "verify_replays"
            replay_status, replay_confirmation = post_test_delivery(port, payload)
            if replay_status != 200 or replay_confirmation != confirmation or hashlib.sha256(saved_path.read_bytes()).hexdigest() != delivery_hash:
                raise DemoError("TEST endpoint replay was not idempotent.")
            replayed = client.record_receipt(item["id"], receipt)
            detail = client.get_outbox(item["id"])
            trace["events"].extend([
                {"method": "POST", "path": f"/v1/outbox/{item['id']}/receipt", "request": receipt, "result": replayed, "replay": True},
                {"method": "GET", "path": f"/v1/outbox/{item['id']}", "result": detail},
            ])
            if replayed != recorded or detail != recorded:
                raise DemoError("The receipt replay or detail response changed the confirmed result.")
            after = client.pending_outbox()
            trace["events"].append({"method": "GET", "path": "/v1/outbox/pending?limit=50", "result": after})
            if after.get("items") != [] or after.get("has_more") is not False:
                raise DemoError("Expected the demo pending outbox to be empty after its receipt.")
            after_stdout, cli_after, after_argv = cli_pending(client.api_url, token)
            if cli_after != after:
                raise DemoError("The CLI and API did not return the same post-receipt outbox.")
            (output / "cli-after.stdout.txt").write_text(after_stdout, encoding="utf-8", newline="")
            persist(output / "outbox-after.json", detail)
            file_count = len(list(saved_path.parent.glob("*.json")))
            if file_count != 1:
                raise DemoError("Expected one durable TEST delivery file after replay.")
            trace.update(passed=True, stage="complete", receipt=receipt, outbox_after=detail,
                         pending_after=after, delivery_sha256=delivery_hash,
                         idempotency={"test_endpoint_replay": True, "receipt_replay": True, "durable_delivery_files": file_count},
                         cli_after={"command": CLI_COMMAND, "argv": after_argv, "stdout_file": "cli-after.stdout.txt", "result": cli_after})
            print("Verified: one durable TEST delivery, identical replays, 0 pending messages.", flush=True)
        return trace
    except Exception as exc:
        message = str(exc).replace(token, "[redacted]") if token else str(exc)
        trace["error"] = {"type": type(exc).__name__, "message": message[:500], "ambiguous": getattr(exc, "ambiguous", False)}
        raise
    finally:
        persist(output / "trace.json", trace)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Root URL of the isolated loopback demo engine")
    parser.add_argument("--output", required=True, type=Path, help="New directory for TEST delivery and evidence")
    args = parser.parse_args()
    token = os.environ.get("RESPAWNED_OUTBOX_TOKEN", "")
    if not token:
        parser.error("Set the demo engine's RESPAWNED_OUTBOX_TOKEN")
    try:
        run(args.url, args.output, token)
        return 0
    except Exception as exc:
        message = str(exc).replace(token, "[redacted]")
        print("TEST delivery: " + message, file=sys.stderr)
        print("Inspect saved evidence before retrying. No external provider was contacted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
