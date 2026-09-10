"""One real-database HTTP journey through import, human review and export."""

import csv
from datetime import UTC, datetime, timedelta
import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from respawned.api import ui
from respawned.api.app import app, get_connection, get_workflow_clock, get_workflow_policy
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.policy import load_policy
from respawned.llm.adapter import LiteLLMAdapter


def test_ingest_review_and_export_preserve_the_approved_message(postgres_connection, tmp_path, monkeypatch):
    now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    opportunity = {
        "id": "workflow-opportunity", "contact_key": "crm:workflow-contact",
        "contact_name": "Avery", "contact_email": "avery@example.com", "owner_name": "Morgan",
        "value": "1250.00", "status": "open", "created_at": (now - timedelta(days=2)).isoformat(),
        "preferred_channel": "email",
    }
    activity = {
        "id": "workflow-reply", "type": "contact_replied", "opportunity_id": opportunity["id"],
        "occurred_at": (now - timedelta(hours=1)).isoformat(), "channel": "email", "direction": "inbound",
    }

    def request_connection():
        with postgres_connection.begin_nested():
            yield postgres_connection

    policy = load_policy(DEFAULT_POLICY_PATH)
    body = "Hi Avery, thanks for your reply. How can Morgan help?"
    approved_body = f"{body}\n\n{policy.drafting.sign_off}"
    completions = []

    def complete(**request):
        completions.append(request)
        return {"choices": [{"message": {"content": body}}]}

    adapter = LiteLLMAdapter(proxy_url="http://unused.test", master_key="test-key",
                            model_alias="test-model", completion_fn=complete)
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "workflow-test-operator")
    monkeypatch.setitem(app.dependency_overrides, get_connection, request_connection)
    monkeypatch.setitem(app.dependency_overrides, get_workflow_clock, lambda: lambda: now)
    monkeypatch.setitem(app.dependency_overrides, get_workflow_policy, lambda: policy)
    monkeypatch.setitem(app.dependency_overrides, ui.get_draft_adapter_factory, lambda: lambda: adapter)
    with TestClient(app, headers={"Authorization": "Bearer workflow-test-operator"}) as client:
        payload = {"opportunities": [opportunity], "activities": [activity]}
        first = client.post("/v1/ingest", json=payload)
        assert first.status_code == 200
        assert first.json() == {"opportunities_upserted": 1, "activities_inserted": 1}
        replay = client.post("/v1/workflow/import", json=payload)
        assert replay.status_code == 200
        assert replay.json() == {"opportunities_upserted": 1, "activities_inserted": 0}
        conflict = client.post("/v1/workflow/import", json={
            "opportunities": [opportunity | {"contact_email": "wrong@example.com"}],
            "activities": [activity | {"type": "content_viewed"}],
        })
        assert conflict.status_code == 409
        assert postgres_connection.execute(text("SELECT contact_email FROM opportunities")).scalar_one() == opportunity["contact_email"]
        assert postgres_connection.execute(text("SELECT type FROM activities")).scalars().all() == ["contact_replied"]
        synced = client.post("/v1/workflow/sync", json={})
        assert synced.status_code == 200 and synced.json()["inserted_count"] == 1
        candidate = client.get("/v1/workflow/queue").json()["items"][0]
        assert candidate["reason"] == "replied_no_answer"
        generated = client.post(f"/v1/workflow/candidates/{candidate['id']}/draft")
        assert generated.status_code == 200
        draft = generated.json()
        assert draft["body"] == approved_body
        assert draft["contact_address"] == opportunity["contact_email"]
        approved = client.post(f"/v1/workflow/drafts/{draft['id']}/approve", json={"review_token": draft["review_token"]})
        assert approved.status_code == 200 and approved.json()["status"] == "approved"
        exported = client.get("/v1/workflow/outbox/export?format=csv")
        assert exported.status_code == 200
        path = tmp_path / "outbox.csv"
        path.write_bytes(exported.content)
    assert len(completions) == 1
    reservation = postgres_connection.execute(text("SELECT contact_address, body, status FROM outbox")).mappings().one()
    assert dict(reservation) == {"contact_address": opportunity["contact_email"], "body": approved_body, "status": "pending"}
    with path.open(newline="", encoding="utf-8") as exported:
        rows = list(csv.DictReader(exported))
    assert len(rows) == 1
    assert rows[0]["contact_address"] == opportunity["contact_email"]
    assert rows[0]["body"] == approved_body and rows[0]["status"] == "pending"
    assert json.loads(rows[0]["opportunity_ids"]) == [opportunity["id"]]
