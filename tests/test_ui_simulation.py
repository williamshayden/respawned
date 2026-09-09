"""The connected-browser harness owns its schema and never needs a provider."""

import importlib.util
import os
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text


def test_connected_fixture_draft_approval_persists_only_inside_owned_schema(postgres_engine, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts" / "serve_ui_simulation.py"
    spec = importlib.util.spec_from_file_location("serve_ui_simulation", path)
    simulation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(simulation)
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "restore-review-token")
    with simulation.simulation_api(postgres_engine.url) as (app, _engine, schema):
        headers = {"Authorization": f"Bearer {simulation.REVIEW_TOKEN}"}
        with TestClient(app) as client:
            records = {item["id"]: item for item in client.get("/v1/ui/records", headers=headers).json()["items"]}
            assert len(records) == 3
            job = records["northstar-backend"]
            assert job["reason"]["code"] == "promised_update_overdue"
            assert "Sep 5, 2026" in job["reason"]["detail"]
            assert {field["label"] for field in job["fields"]} == {"Company", "Role"}
            assert records["northstar-platform"]["candidate_id"] is None
            assert records["northstar-platform"]["contact"] is None
            assert client.get("/v1/ui/inbox", headers=headers).json()["total"] == 1
            response = client.post("/v1/ui/records/northstar-backend/draft", headers=headers)
            assert response.status_code == 200, response.text
            draft = response.json()
            assert "Hi Maya" in draft["body"]
            approved = client.post(f"/v1/ui/drafts/{draft['id']}/approve", headers=headers,
                                   json={"review_token": draft["review_token"]})
            assert approved.status_code == 200, approved.text
            assert approved.json()["outbox_id"] is not None
            outbox = client.get("/v1/ui/outbox", headers=headers).json()["items"]
            assert len(outbox) == 1 and outbox[0]["status"] == "pending"
            assert outbox[0]["sent_at"] is None and outbox[0]["authorization_mode"] == "human"
            client.post("/v1/ui/sync", headers=headers, json={})
            latest = client.get("/v1/ui/records", headers=headers).json()["items"]
            assert next(item for item in latest if item["id"] == "northstar-backend")["next_action"] == "approved"
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT to_regnamespace(:schema)"), {"schema": schema}).scalar_one() is None
    assert os.environ["RESPAWNED_REVIEW_TOKEN"] == "restore-review-token"
