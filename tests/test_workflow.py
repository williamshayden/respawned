"""One real-database journey across the public ingestion and review boundaries."""

from contextlib import contextmanager
import csv
from datetime import UTC, datetime, timedelta
from io import StringIO
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from rich.console import Console
from sqlalchemy import text

from respawned.api.app import app, get_connection
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.cli.outbox import export_outbox
from respawned.cli.review import ReviewSummary, run_review
from respawned.core.policy import load_policy
from respawned.core.sync import sync_candidates
from respawned.llm.adapter import LiteLLMAdapter


def test_ingest_review_and_export_preserve_the_approved_message(
    postgres_connection, tmp_path, monkeypatch
):
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    opportunity = {
        "id": "workflow-opportunity",
        "contact_key": "crm:workflow-contact",
        "contact_name": "Avery",
        "contact_email": "avery@example.com",
        "owner_name": "Morgan",
        "value": "1250.00",
        "status": "open",
        "created_at": (now - timedelta(days=2)).isoformat(),
        "preferred_channel": "email",
    }
    activity = {
        "id": "workflow-reply",
        "type": "contact_replied",
        "opportunity_id": opportunity["id"],
        "occurred_at": (now - timedelta(hours=1)).isoformat(),
        "channel": "email",
        "direction": "inbound",
    }

    @contextmanager
    def transaction():
        # Preserve request/CLI rollback boundaries inside the fixture's outer
        # transaction, which removes every test record after this journey.
        with postgres_connection.begin_nested():
            yield postgres_connection

    def request_connection():
        with transaction() as connection:
            yield connection

    monkeypatch.setitem(app.dependency_overrides, get_connection, request_connection)
    with TestClient(app) as client:
        payload = {"opportunities": [opportunity], "activities": [activity]}
        first = client.post("/v1/ingest", json=payload)
        assert first.status_code == 200
        assert first.json() == {"opportunities_upserted": 1, "activities_inserted": 1}
        replay = client.post("/v1/ingest", json=payload)
        assert replay.status_code == 200
        assert replay.json() == {"opportunities_upserted": 1, "activities_inserted": 0}
        conflict = client.post(
            "/v1/ingest",
            json={
                "opportunities": [opportunity | {"contact_email": "wrong@example.com"}],
                "activities": [activity | {"type": "content_viewed"}],
            },
        )
        assert conflict.status_code == 409

    assert postgres_connection.execute(
        text("SELECT contact_email FROM opportunities")
    ).scalar_one() == opportunity["contact_email"]
    assert postgres_connection.execute(
        text("SELECT type FROM activities")
    ).scalars().all() == ["contact_replied"]

    policy = load_policy(DEFAULT_POLICY_PATH)
    synced = sync_candidates(postgres_connection, now=now, policy=policy)
    assert synced.inserted_count == len(synced.candidates) == 1
    assert synced.candidates[0].reason == "replied_no_answer"

    body = "Hi Avery, thanks for your reply. How can Morgan help?"
    approved_body = f"{body}\n\n{policy.drafting.sign_off}"
    completions = []

    def complete(**request):
        completions.append(request)
        return {"choices": [{"message": {"content": body}}]}

    display = StringIO()
    console = Console(file=display, width=120, force_terminal=False, no_color=True)

    def approve_displayed_message(*_args, **_kwargs):
        assert body in display.getvalue()
        assert opportunity["contact_email"] in display.getvalue()
        return "a"

    summary = run_review(
        SimpleNamespace(begin=transaction),
        now=now,
        policy=policy,
        adapter=LiteLLMAdapter(
            proxy_url="http://unused.test",
            master_key="test-key",
            model_alias="test-model",
            completion_fn=complete,
        ),
        console=console,
        action_prompt=approve_displayed_message,
    )
    assert summary == ReviewSummary(presented=1, approved=1)
    assert len(completions) == 1
    reservation = postgres_connection.execute(
        text("SELECT contact_address, body, status FROM outbox")
    ).mappings().one()
    assert dict(reservation) == {
        "contact_address": opportunity["contact_email"],
        "body": approved_body,
        "status": "pending",
    }
    assert postgres_connection.execute(
        text("SELECT status FROM drafts")
    ).scalar_one() == "approved"

    path = tmp_path / "outbox.csv"
    assert export_outbox(postgres_connection, path) == 1
    with path.open(newline="", encoding="utf-8") as exported:
        rows = list(csv.DictReader(exported))
    assert len(rows) == 1
    assert rows[0]["contact_address"] == opportunity["contact_email"]
    assert rows[0]["body"] == approved_body
    assert rows[0]["status"] == "pending"
    assert json.loads(rows[0]["opportunity_ids"]) == [opportunity["id"]]
