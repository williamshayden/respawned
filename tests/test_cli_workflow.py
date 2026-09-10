"""Qualify the installed CLI through subprocesses, with a loopback model stub.

All application writes use public CLI commands. SQL only creates/removes owned
schemas and inspects persisted outcomes. Legacy fixtures explicitly use `demo`;
source-neutral imports continue to use the canonical HTTP/Python API.
"""

import csv
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli(postgres_engine, tmp_path):
    schema = f"respawned_cli_{uuid4().hex}"
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(postgres_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    url = postgres_engine.url
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DB_", "RESPAWNED_", "LITELLM_", "PGOPTIONS"))}
    environment.update(DB_HOST=url.host, DB_PORT=str(url.port or 5432), DB_USER=url.username,
                       DB_PASSWORD=url.password or "", DB_NAME=url.database,
                       PGOPTIONS=f"-csearch_path={schema}", NO_COLOR="1")
    state = SimpleNamespace(engine=engine, environment=environment, directory=tmp_path,
                            commands=[], processes=[], requests=[], mode="valid")

    class ModelStub(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append({"path": self.path, "request": request})
            body = "Hello [NAME]" if state.mode == "invalid" else "Hi Avery, Morgan here. What time works for you?"
            payload = {"error": {"message": "Deliberate qualification failure", "type": "server_error"}} if state.mode == "failure" else {
                "id": "explicit-cli-test-stub", "object": "chat.completion", "created": 0,
                "model": "cli-test-stub", "choices": [{"index": 0, "message": {"role": "assistant", "content": body}, "finish_reason": "stop"}],
            }
            encoded = json.dumps(payload).encode()
            self.send_response(503 if state.mode == "failure" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelStub)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment.update(LITELLM_PROXY_URL=f"http://127.0.0.1:{server.server_port}/v1",
                       LITELLM_MASTER_KEY="explicit-local-test-only", LITELLM_MODEL_ALIAS="cli-test-stub")

    def run(*arguments, input="", expected=0):
        result = subprocess.run([sys.executable, "-m", "respawned", *arguments], cwd=ROOT,
                                env=environment, input=input, capture_output=True, text=True, timeout=20)
        state.commands.append({"arguments": arguments, "stdin": input, "exit_code": result.returncode,
                               "stdout": result.stdout, "stderr": result.stderr})
        assert result.returncode == expected, result.stdout + result.stderr
        return result.stdout

    def rows(table):
        assert table in {"opportunities", "activities", "sync_runs", "candidates", "drafts", "outbox"}
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()]

    def seed(records, events=()):
        directory = tmp_path / f"seed-{uuid4().hex}"
        directory.mkdir()
        (directory / "quotes.json").write_text(json.dumps(records))
        (directory / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events))
        return run("demo", "--seed-dir", str(directory))

    state.run, state.rows, state.seed = run, rows, seed
    try:
        run("init")
        yield state
    finally:
        for process in state.processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        (tmp_path / "cli-evidence.json").write_text(json.dumps({"commands": state.commands, "model_stub_requests": state.requests}, indent=2))
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def record(identity="one", **changes):
    return {"id": identity, "customer_name": "Avery", "customer_phone": "+13125550101",
            "tech_name": "Morgan", "status": "open", "amount": 100,
            "created_at": (NOW - timedelta(days=5)).isoformat(), **changes}


def event(identity="one", **changes):
    return {"event_id": f"{identity}:reply", "quote_id": identity, "type": "customer_replied",
            "timestamp": (NOW - timedelta(hours=1)).isoformat(), "direction": "inbound", "channel": "sms", **changes}


def review(cli, actions, **kwargs):
    return cli.run("review", "--now", NOW.isoformat(), input=actions, **kwargs)


def test_empty_queue_and_missing_model_are_handled_without_generation(cli):
    cli.environment.pop("LITELLM_MASTER_KEY")
    assert "Processed 0 drafts" in review(cli, "")
    cli.seed([record()], [event()])
    cli.run("sync", "--now", NOW.isoformat())
    output = review(cli, "")
    assert "Drafting model is not configured" in output and "1 blocked" in output
    assert cli.rows("drafts") == cli.rows("outbox") == cli.requests == []


def test_subprocess_cli_replay_review_edit_reject_and_repeatable_unsent_export(cli):
    records = [record(), record("two", customer_phone="+13125550102", amount=0)]
    events = [event(), event("two")]
    cli.seed(records, events)
    assert "0 new activities" in cli.seed(records, events)
    assert len(cli.rows("opportunities")) == len(cli.rows("activities")) == 2
    assert "Selected 2 candidates" in cli.run("sync", "--dry-run", "--now", NOW.isoformat())
    assert cli.rows("sync_runs") == cli.rows("candidates") == []
    cli.run("sync", "--now", NOW.isoformat())
    assert "persisted 0 new candidates" in cli.run("sync", "--now", NOW.isoformat())
    assert "2 skipped" in review(cli, "s\ns\n")
    assert len(cli.requests) == 2 and all(item["path"] == "/v1/chat/completions" for item in cli.requests)
    # A broken optional backend must not prevent human decisions on saved copy.
    cli.environment.pop("LITELLM_MASTER_KEY")
    cli.environment["RESPAWNED_MODEL_BACKEND"] = "invalid-backend"
    edited = "Hi Avery, Morgan here. Thursday afternoon works for me."
    output = review(cli, f"e\n{edited}\na\nr\n")
    assert "1 approved, 1 rejected" in output
    assert len(cli.requests) == 2
    rows = cli.rows("outbox")
    assert len(rows) == 1 and rows[0]["body"] == edited
    assert rows[0]["status"] == "pending" and rows[0]["sent_at"] is None
    assert rows[0]["authorization_mode"] == "human" and rows[0]["contact_address"] == "+13125550101"
    first, second = cli.directory / "first.csv", cli.directory / "second.csv"
    cli.run("outbox", "--path", str(first))
    cli.run("outbox", "--path", str(second))
    assert first.read_bytes() == second.read_bytes()
    with first.open(newline="") as stream:
        exported = list(csv.DictReader(stream))
    assert len(exported) == 1 and exported[0]["body"] == edited
    assert cli.rows("outbox") == rows
    inbox = json.loads(cli.run("inbox", "--json", "--now", NOW.isoformat()))
    assert inbox["total"] == 2  # Unsent approval is not evidence of an answer.
    assert "Selected 0 candidates" in cli.run("sync", "--now", NOW.isoformat())
    sent = event(event_id="one:sent", type="message_sent", direction="outbound",
                 timestamp=(NOW - timedelta(minutes=1)).isoformat())
    cli.seed(records, [*events, sent])
    assert json.loads(cli.run("inbox", "--json", "--now", NOW.isoformat()))["total"] == 1
    assert cli.rows("outbox") == rows  # Importing an outbound event does not fabricate delivery acknowledgement.


def test_subprocess_cli_model_failures_persist_nothing_and_valid_retry_recovers(cli):
    cli.seed([record()], [event()])
    cli.run("sync", "--now", NOW.isoformat())
    for mode in ("failure", "invalid"):
        cli.mode = mode
        assert "1 blocked" in review(cli, "")
        assert cli.rows("drafts") == cli.rows("outbox") == []
    cli.mode = "valid"
    assert "1 skipped" in review(cli, "s\n")
    assert len(cli.requests) == 3 and len(cli.rows("drafts")) == 1
    assert cli.rows("outbox") == []


def start_review_at_prompt(cli):
    arguments = [sys.executable, "-m", "respawned", "review", "--now", NOW.isoformat()]
    process = subprocess.Popen(arguments, cwd=ROOT, env=cli.environment,
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
def test_subprocess_cli_rechecks_stale_display_before_approval(cli, change):
    original = record()
    cli.seed([original], [event()])
    cli.run("sync", "--now", NOW.isoformat())
    process, displayed = start_review_at_prompt(cli)
    assert b"Draft message" in displayed and b"+13125550101" in displayed
    if change == "recipient":
        cli.seed([original | {"customer_phone": "+13125550999"}], [event()])
    elif change == "closed":
        cli.seed([original | {"status": "dismissed"}], [event()])
    elif change == "cooldown":
        cli.seed([original], [event(), event(event_id="sent-during-review", type="message_sent", direction="outbound",
                                             timestamp=(NOW - timedelta(minutes=1)).isoformat())])
    else:
        assert "1 skipped" in review(cli, "e\nHi Avery, Morgan has updated the exact review text.\ns\n")
    remaining, _ = process.communicate(b"a\n", timeout=15)
    output = (displayed + remaining).decode()
    cli.commands.append({"arguments": process.args, "stdout": output,
                         "exit_code": process.returncode, "concurrent_change": change})
    assert process.returncode == 0 and "1 blocked" in output and "0 approved" in output
    assert cli.rows("outbox") == []
    assert len(cli.requests) == 1
