"""CLI subprocesses use the real HTTP API with no client-side database access."""

from datetime import UTC, datetime, timedelta
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import csv
import json
import os
from pathlib import Path
from queue import Queue
import socket
import subprocess
import sys
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, text

from http_engine import serve_http
from respawned.api import app as api_module, ui
from respawned.client import RespawnedClient
from respawned.config import DEFAULT_POLICY_PATH
from respawned.core.policy import ReviewPolicy, load_policy
from respawned.db.helpers.pg_connect import create_tables
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli(postgres_engine, tmp_path, monkeypatch):
    schema = f"respawned_cli_{uuid4().hex}"
    engine = create_engine(postgres_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    state = SimpleNamespace(engine=engine, now=NOW, policy=load_policy(DEFAULT_POLICY_PATH),
                            model_enabled=False, model_calls=[], model_resolutions=0, mode="valid",
                            commands=[], processes=[], requests=[], directory=tmp_path)
    previous = dict(api_module.app.dependency_overrides)
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "cli-http-operator")
    monkeypatch.setenv("RESPAWNED_OUTBOX_TOKEN", "cli-http-connector")
    monkeypatch.delenv("RESPAWNED_PROCESS_TOKEN", raising=False)

    def complete(**request):
        state.model_calls.append(request)
        if state.mode == "failure":
            raise RuntimeError("Deliberate qualification failure")
        body = "Hello [NAME]" if state.mode == "invalid" else "Hi Avery, Morgan here. What time works for you?"
        return {"choices": [{"message": {"content": body}}]}

    def model():
        state.model_resolutions += 1
        if not state.model_enabled:
            raise HTTPException(503, "Drafting model is not configured")
        return LiteLLMAdapter("http://unused.invalid", "unused", "explicit-test-stub", completion_fn=complete)

    api_module.app.dependency_overrides[api_module.get_api_engine] = lambda: engine
    api_module.app.dependency_overrides[api_module.get_workflow_policy] = lambda: state.policy
    api_module.app.dependency_overrides[api_module.get_workflow_clock] = lambda: lambda: state.now
    api_module.app.dependency_overrides[ui.get_draft_adapter_factory] = lambda: model
    api_module.app.dependency_overrides[api_module.get_workflow_adapter] = model
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DB_", "RESPAWNED_", "LITELLM_", "PGOPTIONS"))}
    environment.update(DB_HOST="client-must-never-resolve.invalid", DB_PORT="invalid-database-port",
                       DB_NAME="not-a-client-setting", DB_USER="invalid-client-user", DB_PASSWORD="invalid-client-password",
                       PYTHONPATH=str(ROOT / "src"), RESPAWNED_REVIEW_TOKEN="cli-http-operator",
                       RESPAWNED_OUTBOX_TOKEN="cli-http-connector", NO_COLOR="1", COLUMNS="120")
    state.environment = environment

    def run(*arguments, input="", expected=0):
        result = subprocess.run([sys.executable, "-m", "respawned", *arguments], cwd=tmp_path,
                                env=environment, input=input, capture_output=True, text=True, timeout=20)
        state.commands.append({"arguments": arguments, "stdin": input, "exit_code": result.returncode,
                               "stdout": result.stdout, "stderr": result.stderr})
        assert result.returncode == expected, result.stdout + result.stderr
        assert "Traceback" not in result.stderr
        return result

    def rows(table):
        assert table in {"opportunities", "activities", "sync_runs", "candidates", "drafts", "outbox", "outbox_receipts"}
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY 1")).mappings()]

    def ingest(records, activities=()):
        return json.loads(run("import", "--file", "-", input=json.dumps({
            "opportunities": records, "activities": list(activities),
        })).stdout)

    def draft(key, body):
        return json.loads(run("draft", key, "--body-file", "-", input=body).stdout)

    state.run, state.rows, state.ingest, state.draft = run, rows, ingest, draft
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        create_tables(engine)
        with serve_http(api_module.app, state.requests) as base_url:
            state.url = base_url
            environment["RESPAWNED_API_URL"] = base_url
            state.api = RespawnedClient(base_url, token="cli-http-operator", outbox_token="cli-http-connector")
            yield state
    finally:
        for process in state.processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        api_module.app.dependency_overrides.clear()
        api_module.app.dependency_overrides.update(previous)
        (tmp_path / "cli-evidence.json").write_text(json.dumps({"commands": state.commands,
                                                               "model_stub_requests": state.model_calls}, indent=2))
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def record(identity="one", **changes):
    return {"id": identity, "contact_key": f"person:{identity}", "contact_name": "Avery",
            "contact_email": f"{identity}@example.com", "owner_name": "Morgan", "status": "open",
            "kind": "community", "created_at": (NOW - timedelta(days=5)).isoformat(), **changes}


def event(identity="one", **changes):
    return {"id": f"{identity}:reply", "opportunity_id": identity, "type": "contact_replied",
            "occurred_at": (NOW - timedelta(hours=1)).isoformat(), "direction": "inbound",
            "channel": "email", "classification": "human", **changes}


def test_empty_queue_and_missing_model_are_handled_without_generation(cli):
    assert "Reviewed 0 drafts" in cli.run("review").stdout
    assert cli.model_resolutions == 0
    cli.ingest([record()], [event()])
    output = cli.run("review", expected=1).stdout
    assert "Drafting model is not configured" in output and "1 blocked" in output
    assert cli.rows("drafts") == cli.rows("outbox") == cli.model_calls == []


def test_subprocess_import_agent_copy_human_review_outbox_and_receipt(cli):
    records, events = [record(), record("two", value="0")], [event(), event("two")]
    assert cli.ingest(records, events) == {"opportunities_upserted": 2, "activities_inserted": 2}
    assert cli.ingest(records, events)["activities_inserted"] == 0
    assert "would save 2 new candidates" in cli.run("sync", "--dry-run").stdout
    assert cli.rows("sync_runs") == cli.rows("candidates") == []
    # Agent copy goes through HTTP and uses server validation without a configured model.
    draft = cli.draft("one", 'Hi Avery, café on Thursday?\nWould "after lunch" suit you?')
    assert draft["body"] == 'Hi Avery, café on Thursday?\nWould "after lunch" suit you?'
    cli.draft("two", "Hi Avery, what time would work for you?")
    cli.policy = replace(cli.policy, review=ReviewPolicy("automatic"))
    edited = "Hi Avery, Friday afternoon works for me."
    output = cli.run("review", input=f"e\n{edited}\na\nr\n").stdout
    assert "1 approved, 1 rejected" in output
    assert "Automatically authorized" not in output
    assert cli.model_resolutions == 0 and cli.model_calls == []
    rows = cli.rows("outbox")
    assert len(rows) == 1 and rows[0]["body"] == edited
    assert rows[0]["authorization_mode"] == "human"
    assert rows[0]["status"] == "pending" and rows[0]["sent_at"] is None
    pending = json.loads(cli.run("outbox", "--pending", "--json", "--limit", "1").stdout)
    assert pending == cli.api.pending_outbox(limit=1)
    assert pending["items"][0]["body"] == edited
    first, second = cli.directory / "nested" / "first.csv", cli.directory / "second.csv"
    cli.run("outbox", "--path", str(first))
    cli.run("outbox", "--path", str(second))
    assert first.read_bytes() == second.read_bytes() == cli.api.export_outbox(format="csv").encode()
    with first.open(newline="") as stream:
        exported = list(csv.DictReader(stream))
    assert len(exported) == 1 and len(exported[0]) == 12 and exported[0]["body"] == edited
    assert cli.rows("outbox") == rows
    assert json.loads(cli.run("inbox", "--json").stdout)["total"] == 2
    cli.now = NOW + timedelta(minutes=2)
    receipt = cli.api.record_receipt(rows[0]["id"], {
        "sender": "cli-qualification", "provider_message_id": "mock-delivery-one", "sent_at": cli.now.isoformat(),
    })
    assert receipt["status"] == "sent"
    assert json.loads(cli.run("outbox", "--json", "--pending").stdout) == {"items": [], "has_more": False}
    history = json.loads(cli.run("outbox", "--json").stdout)
    assert history["items"][0]["status"] == "sent" and history["items"][0]["body"] == edited
    assert json.loads(cli.run("inbox", "--json").stdout)["total"] == 1
    cli.run("outbox", "--path", str(first))
    assert first.read_bytes() == cli.api.export_outbox(format="csv").encode()
    assert len(cli.rows("outbox_receipts")) == 1


def test_import_and_draft_accept_files_and_api_url_after_command(cli):
    batch = cli.directory / "records.json"
    batch.write_text(json.dumps({"opportunities": [record()], "activities": [event()]}))
    text_file = cli.directory / "draft.txt"
    body = "Hi Avery, Thursday works.\r\nCafé after lunch?"
    text_file.write_bytes(body.encode())
    cli.environment["RESPAWNED_API_URL"] = "https://must-not-be-used.invalid"
    imported = cli.run("--timeout", "5", "import", "--api-url", cli.url, "--file", str(batch))
    assert json.loads(imported.stdout)["opportunities_upserted"] == 1
    drafted = cli.run("--api-url", cli.url, "draft", "one", "--body-file", str(text_file))
    assert json.loads(drafted.stdout)["body"] == body
    assert cli.model_resolutions == 0


def test_cli_api_errors_are_nonzero_without_partial_json_or_secret_leaks(cli):
    cli.environment["RESPAWNED_REVIEW_TOKEN"] = "wrong-cli-credential"
    denied = cli.run("import", "--file", "-", input=json.dumps({"opportunities": [record()]}), expected=1)
    assert denied.stdout == "" and "HTTP 401" in denied.stderr
    assert "wrong-cli-credential" not in denied.stderr
    assert cli.rows("opportunities") == []
    existing_export = cli.directory / "existing.csv"
    existing_export.write_bytes(b"keep this existing export")
    failed_export = cli.run("outbox", "--path", str(existing_export), expected=1)
    assert failed_export.stdout == "" and existing_export.read_bytes() == b"keep this existing export"
    cli.environment["RESPAWNED_REVIEW_TOKEN"] = "cli-http-operator"
    before = len(cli.requests)
    invalid_json = cli.run("import", "--file", "-", input="{", expected=1)
    assert invalid_json.stdout == "" and "valid JSON" in invalid_json.stderr
    assert len(cli.requests) == before
    cli.ingest([record()], [event()])
    invalid = cli.run("draft", "one", "--body-file", "-", input="Hello [NAME]", expected=1)
    assert invalid.stdout == "" and "HTTP 422" in invalid.stderr
    assert cli.rows("drafts") == []


def test_model_failures_are_server_results_and_supplied_copy_recovers(cli):
    cli.model_enabled = True
    cli.ingest([record()], [event()])
    for mode in ("failure", "invalid"):
        cli.mode = mode
        failed = cli.run("draft", "one", expected=1)
        assert failed.stdout == "" and "HTTP" in failed.stderr
        assert cli.rows("drafts") == cli.rows("outbox") == []
    assert len(cli.model_calls) == 2
    draft = cli.draft("one", "Hi Avery, when would you like to speak?")
    assert draft["status"] == "pending" and len(cli.model_calls) == 2
    assert "1 skipped" in cli.run("review", input="s\n").stdout


def start_review_at_prompt(cli):
    arguments = [sys.executable, "-m", "respawned", "review"]
    process = subprocess.Popen(arguments, cwd=cli.directory, env=cli.environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    cli.processes.append(process)
    output = Queue()

    def read_prompt():
        displayed = b""
        while b"Action: [A]pprove, [R]eject, [E]dit, [S]kip:" not in displayed:
            chunk = process.stdout.read(1)
            if not chunk:
                break
            displayed += chunk
        output.put(displayed)

    reader = Thread(target=read_prompt, daemon=True)
    reader.start()
    displayed = output.get(timeout=15)
    reader.join(timeout=1)
    assert b"Action: [A]pprove, [R]eject, [E]dit, [S]kip:" in displayed, displayed.decode()
    return process, displayed


@pytest.mark.parametrize("change", ["recipient", "closed", "cooldown", "concurrent-edit"])
def test_cli_review_never_reapproves_after_stale_display(cli, change):
    original = record()
    cli.ingest([original], [event()])
    draft = cli.draft("one", "Hi Avery, would Thursday work?")
    process, displayed = start_review_at_prompt(cli)
    assert b"one@example.com" in displayed
    if change == "recipient":
        cli.ingest([original | {"contact_email": "another@example.com"}], [event()])
    elif change == "closed":
        cli.ingest([original | {"status": "lost"}], [event()])
    elif change == "cooldown":
        cli.ingest([original], [event(id="one:sent", type="message_sent", direction="outbound",
                                      occurred_at=(NOW - timedelta(minutes=1)).isoformat())])
    else:
        cli.api.edit_draft(draft["id"], "Hi Avery, Friday works instead.", draft["review_token"])
    remaining, _ = process.communicate(b"a\n", timeout=15)
    output = (displayed + remaining).decode()
    assert process.returncode == 1 and "1 blocked" in output and "0 approved" in output
    assert cli.rows("outbox") == []
    approvals = [item for item in cli.requests if item["path"].endswith("/approve")]
    assert len(approvals) == 1
    assert json.loads(approvals[0]["body"])["review_token"] == draft["review_token"]
    assert cli.model_resolutions == 0


def test_cli_does_not_retry_a_write_after_connection_drops(tmp_path):
    requests = []

    class LostReply(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), LostReply)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {**os.environ, "RESPAWNED_REVIEW_TOKEN": "ambiguous-write-test",
                   "RESPAWNED_API_URL": f"http://127.0.0.1:{server.server_port}", "PYTHONPATH": str(ROOT / "src"),
                   "DB_PORT": "invalid-database-port"}
    try:
        result = subprocess.run([sys.executable, "-m", "respawned", "draft", "one", "--body-file", "-", "--timeout", "1"],
                                cwd=tmp_path, env=environment, input="Hi Avery, when would you like to speak?",
                                capture_output=True, text=True, timeout=10)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.returncode == 1 and result.stdout == ""
    assert "result is unknown" in result.stderr.lower() and "no retry was made" in result.stderr
    assert "Traceback" not in result.stderr
    assert len(requests) == 1 and requests[0][0] == "/v1/workflow/records/one/draft"


def test_demo_command_imports_legacy_sample_files_through_http(cli):
    seed = cli.directory / "samples"
    seed.mkdir()
    (seed / "quotes.json").write_text(json.dumps([{
        "id": "demo-one", "customer_name": "Avery", "customer_phone": "+13125550101",
        "tech_name": "Morgan", "status": "open", "amount": 100,
        "created_at": (NOW - timedelta(days=5)).isoformat(),
    }]))
    (seed / "events.jsonl").write_text(json.dumps({
        "event_id": "demo-one:reply", "quote_id": "demo-one", "type": "customer_replied",
        "timestamp": (NOW - timedelta(hours=1)).isoformat(), "direction": "inbound", "channel": "sms",
    }) + "\n")
    first = json.loads(cli.run("demo", "--seed-dir", str(seed)).stdout)
    assert first == {"opportunities_upserted": 1, "activities_inserted": 1}
    assert json.loads(cli.run("demo", "--seed-dir", str(seed)).stdout)["activities_inserted"] == 0
    assert len(cli.rows("opportunities")) == len(cli.rows("activities")) == 1
    assert cli.rows("sync_runs") == cli.rows("drafts") == cli.rows("outbox") == []
    assert cli.model_resolutions == 0


def test_process_command_obeys_the_server_policy_and_reports_json(cli):
    cli.model_enabled = True
    cli.ingest([record()], [event()])
    prepared = json.loads(cli.run("process", "--limit", "1").stdout)
    assert prepared["review_mode"] == "human" and prepared["items"][0]["status"] == "pending"
    assert cli.rows("outbox") == []
    cli.policy = replace(cli.policy, review=ReviewPolicy("automatic"))
    automatic = json.loads(cli.run("process", "--limit", "1").stdout)
    assert automatic["review_mode"] == "automatic" and automatic["items"][0]["status"] == "authorized"
    assert cli.rows("outbox")[0]["authorization_mode"] == "automatic"
    assert len(cli.model_calls) == 1
