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
import os
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
import uvicorn

from respawned.api.app import (
    app, get_api_engine, get_workflow_adapter, get_workflow_clock, get_workflow_policy,
)
from respawned.api.ui import get_draft_adapter_factory
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.ingest import ingest_records
from respawned.core.policy import ReviewPolicy, load_policy
from respawned.core.sync import sync_candidates
from respawned.db.helpers.pg_connect import create_tables
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
REVIEW_TOKEN = "ui-simulation-review-token"


def seed_records():
    """Two named contacts plus a same-company application without any contact."""
    return {
        "opportunities": [
            {
                "id": "northstar-backend", "kind": "job_application",
                "title": "Backend Engineer at Northstar", "status": "open",
                "created_at": "2026-08-25T14:00:00Z",
                "contact_key": "simulation:maya", "contact_name": "Maya Chen",
                "contact_email": "maya@northstar.example", "preferred_channel": "email",
                "context": {
                    "company": "Northstar", "role": "Backend Engineer", "stage": "Interviewing",
                    "expected_reply_at": "2026-09-05T17:00:00Z",
                    "summary": "Completed the team interview; Maya expected to share next steps on September 5.",
                    "source_url": "https://example.com/simulation/northstar-backend",
                },
            },
            {
                "id": "evergreen-proposal", "kind": "sales", "title": "Website project for Evergreen",
                "status": "open", "created_at": "2026-08-28T15:00:00Z",
                "contact_key": "simulation:alex", "contact_name": "Alex Rivera",
                "contact_email": "alex@evergreen.example", "preferred_channel": "email",
                "owner_name": "Jordan", "value": 8400,
                "context": {"company": "Evergreen", "stage": "Proposal sent",
                            "source_url": "https://example.com/simulation/evergreen-proposal"},
            },
            {
                "id": "northstar-platform", "kind": "job_application",
                "title": "Platform Engineer at Northstar", "status": "open",
                "created_at": "2026-09-01T15:00:00Z",
                "context": {"company": "Northstar", "role": "Platform Engineer",
                            "stage": "Applied", "source_url": "https://example.com/simulation/northstar-platform"},
            },
        ],
        "activities": [
            {
                "id": "maya:interview-update", "opportunity_id": "northstar-backend",
                "type": "contact_replied", "direction": "inbound", "channel": "email",
                "occurred_at": "2026-09-02T15:30:00Z", "classification": "human",
                "summary": "Thanks for meeting the team. I expect to share next steps by September 5.",
                "source_url": "https://example.com/simulation/mail/maya-update",
            },
            {
                "id": "maya:acknowledged", "opportunity_id": "northstar-backend",
                "type": "message_sent", "direction": "outbound", "channel": "email",
                "occurred_at": "2026-09-03T14:00:00Z", "classification": "human",
                "summary": "Thank you, Maya. I enjoyed the discussion and look forward to hearing about next steps.",
                "source_url": "https://example.com/simulation/mail/maya-thanks",
            },
            {
                "id": "alex:question", "opportunity_id": "evergreen-proposal",
                "type": "email_received", "direction": "inbound", "channel": "email",
                "occurred_at": "2026-09-08T16:00:00Z", "classification": "human",
                "summary": "The proposal looks good. Could we discuss the timeline this week?",
                "source_url": "https://example.com/simulation/mail/alex-question",
            },
            {
                "id": "platform:receipt", "opportunity_id": "northstar-platform",
                "type": "application_received", "direction": "inbound", "channel": "email",
                "occurred_at": "2026-09-01T15:01:00Z", "classification": "automated",
                "summary": "We received your application. This automated mailbox does not accept replies.",
                "source_url": "https://example.com/simulation/mail/platform-receipt",
            },
        ],
    }


def _completion(**kwargs):
    context = str(kwargs.get("messages", ""))
    if "Maya" in context:
        body = "Hi Maya, I enjoyed meeting the team for the Backend Engineer role. You mentioned an update after September 5. Is there any news on next steps?"
    else:
        body = "Hi Alex, thanks for reviewing the proposal. I would be happy to discuss the timeline. Would Thursday afternoon work for you?"
    return {"choices": [{"message": {"content": body}}]}


@contextmanager
def simulation_api(postgres_url):
    """Own the complete temporary schema and restore process settings on exit."""
    url = make_url(postgres_url)
    if url.get_backend_name() != "postgresql" or url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("UI simulation requires an explicitly supplied loopback PostgreSQL URL")
    schema = f"respawned_ui_simulation_{uuid4().hex}"
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"},
                           pool_size=3, max_overflow=0)
    previous = app.dependency_overrides.copy()
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
        os.environ["RESPAWNED_REVIEW_TOKEN"] = REVIEW_TOKEN
        os.environ["RESPAWNED_PROCESS_TOKEN"] = ""
        yield app, engine, schema
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
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
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    with simulation_api(args.postgres_url) as (application, _engine, schema):
        print(f"Synthetic UI API: http://127.0.0.1:{args.port}", flush=True)
        print(f"Simulation-only review token: {REVIEW_TOKEN}", flush=True)
        print(f"Owned temporary schema: {schema}; removed on normal shutdown", flush=True)
        uvicorn.run(application, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
