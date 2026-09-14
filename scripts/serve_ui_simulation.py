"""Serve a connected review UI with synthetic records and deterministic copy.

An explicitly supplied loopback PostgreSQL database holds one temporary schema.
No provider, mailbox, delivery service, or caller .env file is used. A normal
server shutdown drops only the schema created by this process.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
import uvicorn

from respawned.api.app import (
    app, get_api_engine, get_workflow_adapter, get_workflow_clock, get_workflow_policy,
)
from respawned.api.ui import get_draft_adapter_factory
from respawned.api.session import LocalSession, SESSION_PROOF_HEADER
from respawned.api import setup as setup_api
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.ingest import ingest_records
from respawned.core.policy import ReviewPolicy, load_policy
from respawned.core.sync import sync_candidates
from respawned.core.settings import load_model_settings
from respawned.db.helpers.pg_connect import create_tables
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
REVIEW_TOKEN = "ui-simulation-review-token"


@contextmanager
def session_source_receiver(manager: LocalSession | None, port: int | None):
    """An owned source destination reports cookie-only replay without retaining secrets."""
    if manager is None or port is None:
        yield
        return

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_GET(self):
            if self.path != "/v1/workflow/source":
                self.send_error(404)
                return
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            fixture_cookie = cookies.get(manager.cookie_name)
            headers = {"Cookie": f"{manager.cookie_name}={fixture_cookie.value}"} if fixture_cookie else {}
            # This receiver deliberately has no access to the origin's proof.
            request = Request(f"{manager.origin}/v1/workflow/config", headers=headers)
            try:
                with urlopen(request, timeout=5) as response:
                    replay_status = response.status
            except HTTPError as error:
                replay_status = error.code
            except URLError:
                replay_status = 0
            body = ("<!doctype html><html><title>Owned session boundary check</title><body>"
                    "<h1>Session boundary check</h1>"
                    f"<p>Fixture cookie received: {'yes' if fixture_cookie else 'no'}</p>"
                    f"<p>Session proof received: {'yes' if self.headers.get(SESSION_PROOF_HEADER) else 'no'}</p>"
                    f"<p>Cookie-only replay: HTTP {replay_status}</p>"
                    "<p>Only disposable fixture credentials are used.</p></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", port), Receiver)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def seed_records():
    fixture = Path(__file__).resolve().parents[1] / "web/tests/fixtures/ui-import.json"
    return json.loads(fixture.read_text())


def _completion(**kwargs):
    context = str(kwargs.get("messages", ""))
    if "Maya" in context:
        body = "Hi Maya, I enjoyed meeting the team for the Backend Engineer role. You mentioned an update after September 5. Is there any news on next steps?"
    else:
        body = "Hi Alex, thanks for reviewing the proposal. I would be happy to discuss the timeline. Would Thursday afternoon work for you?"
    return {"choices": [{"message": {"content": body}}]}


@contextmanager
def simulation_api(postgres_url, *, empty=False):
    """Own the complete temporary schema and restore process settings on exit."""
    url = make_url(postgres_url)
    if url.get_backend_name() != "postgresql" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("UI simulation requires an explicitly supplied loopback PostgreSQL URL")
    schema = f"respawned_ui_simulation_{uuid4().hex}"
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"},
                           pool_size=3, max_overflow=0)
    previous = app.dependency_overrides.copy()
    previous_probe = setup_api.probe_setup_database
    previous_tokens = {key: os.environ.get(key) for key in ("RESPAWNED_REVIEW_TOKEN", "RESPAWNED_PROCESS_TOKEN")}
    created = False
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        created = True
        create_tables(engine)
        policy = load_policy(DEFAULT_POLICY_PATH)
        policy = replace(policy, review=ReviewPolicy("human"),
                         drafting=replace(policy.drafting, sign_off="Jordan"))
        adapter = LiteLLMAdapter("http://simulation.invalid", "unused", "deterministic-ui-stub",
                                completion_fn=_completion)
        if not empty:
            with engine.begin() as connection:
                ingest_records(connection, **seed_records())
                sync_candidates(connection, now=NOW, policy=policy, limit=200)
        app.dependency_overrides.update({
            get_api_engine: lambda: engine,
            get_workflow_policy: lambda: policy,
            get_workflow_clock: lambda: lambda: NOW,
            get_workflow_adapter: lambda: adapter,
            get_draft_adapter_factory: lambda: lambda: adapter,
        })
        # Setup uses an independent bounded connection in normal operation. Keep
        # this harness's status/settings reads inside its owned schema as well.
        def probe_owned_database():
            with engine.connect() as connection:
                return load_model_settings(connection)

        setup_api.probe_setup_database = probe_owned_database
        os.environ["RESPAWNED_REVIEW_TOKEN"] = REVIEW_TOKEN
        os.environ["RESPAWNED_PROCESS_TOKEN"] = ""
        yield app, engine, schema
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        setup_api.probe_setup_database = previous_probe
        for key, value in previous_tokens.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            if created:
                with engine.begin() as connection:
                    connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-url", required=True, help="Owned loopback PostgreSQL test database")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--empty", action="store_true", help="Start without records so the browser can exercise import")
    parser.add_argument("--local-session", action="store_true", help="Use the real local browser session boundary")
    parser.add_argument("--launch-url-file", type=Path, help="Write the one-use fixture launch URL to a new private file")
    parser.add_argument("--source-receiver-port", type=int, help="Serve an owned cross-port cookie replay check")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.local_session != bool(args.launch_url_file):
        parser.error("--local-session and --launch-url-file must be used together")
    if args.source_receiver_port is not None and (not args.local_session or not 1 <= args.source_receiver_port <= 65535 or args.source_receiver_port == args.port):
        parser.error("--source-receiver-port requires a local session and a separate valid port")
    with simulation_api(args.postgres_url, empty=args.empty) as (application, _engine, schema):
        manager = LocalSession(args.port) if args.local_session else None
        previous_session = getattr(application.state, "local_session", None)
        launch_file_created = False
        try:
            if manager is not None:
                application.state.local_session = manager
                descriptor = os.open(args.launch_url_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                launch_file_created = True
                with os.fdopen(descriptor, "w") as output:
                    output.write(manager.launch_url + "\n")
                print(f"One-use fixture launch URL written to {args.launch_url_file}", flush=True)
            else:
                print(f"Simulation-only review token: {REVIEW_TOKEN}", flush=True)
            print(f"Synthetic UI API: http://127.0.0.1:{args.port}", flush=True)
            print(f"Owned temporary schema: {schema}; removed on normal shutdown", flush=True)
            with session_source_receiver(manager, args.source_receiver_port):
                uvicorn.run(application, host="127.0.0.1", port=args.port, log_level="info",
                            proxy_headers=False, timeout_graceful_shutdown=5)
        finally:
            if manager is not None:
                manager.close()
                application.state.local_session = previous_session
            if launch_file_created:
                args.launch_url_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
