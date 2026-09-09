"""Check connector acknowledgment and mock-provider boundaries without model calls."""

import importlib
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest


@pytest.fixture
def simulation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("simulate_connector")


@pytest.mark.parametrize("status,body", [
    (202, {"opportunities_upserted": 1, "activities_inserted": 2}),
    (200, {"error": "not committed"}),
    (200, "upstream proxy error"),
    (200, {"opportunities_upserted": True, "activities_inserted": 2}),
    (200, {"opportunities_upserted": "1", "activities_inserted": 2}),
    (200, {"opportunities_upserted": 0, "activities_inserted": 2}),
    (200, {"opportunities_upserted": 1, "activities_inserted": False}),
    (200, {"opportunities_upserted": 1, "activities_inserted": -1}),
    (200, {"opportunities_upserted": 1, "activities_inserted": 3}),
])
def test_invalid_acknowledgment_retains_batch_for_restart(simulation, tmp_path, status, body):
    page = simulation.source_page("website", "avery") | {"next_cursor": 1}
    reads, mappings, posts = [], [], []

    def source_response(request):
        reads.append(request)
        return httpx.Response(200, json=page)

    def map_page(value):
        mappings.append(value)
        return simulation.fixture_mapping(value)

    def engine_response(request):
        posts.append(request.content)
        if len(posts) > 1:
            return httpx.Response(200, json={"opportunities_upserted": 1, "activities_inserted": 0})
        return (httpx.Response(status, text=body) if isinstance(body, str)
                else httpx.Response(status, json=body))

    state_path = tmp_path / "checkpoint.json"
    with (httpx.Client(base_url="http://source", transport=httpx.MockTransport(source_response)) as source,
          httpx.Client(base_url="http://engine", transport=httpx.MockTransport(engine_response)) as engine):
        connector = simulation.Connector(source, engine, state_path, map_page, [])
        with pytest.raises(ValueError, match="checkpoint retained"):
            connector.sync_once()
        assert connector.state()["cursor"] == 0
        assert connector.state()["pending"] is not None

        # Even an unsuccessful acknowledgment must not rerun extraction after restart.
        restarted = simulation.Connector(source, engine, state_path, map_page, [])
        assert restarted.sync_once()["activities_inserted"] == 0
        assert restarted.state() == {"cursor": 1, "pending": None}
    assert len(reads) == len(mappings) == 1
    assert posts[0] == posts[1]


@pytest.mark.parametrize("changed", [{"body": "Different copy"}, {"to": "other@example.com"}])
def test_mock_draft_replay_rejects_changed_copy_or_destination(simulation, changed):
    mailbox = simulation.Mailbox()
    payload = {"external_key": "engine:1", "to": "avery@example.com", "body": "Approved copy"}
    with TestClient(mailbox.app) as client:
        first = client.post("/drafts", json=payload)
        assert first.status_code == 200
        assert client.post("/drafts", json=payload).json() == first.json()
        assert client.post("/drafts", json=payload | changed).status_code == 409
        assert client.get("/drafts").json() == {"items": [payload], "sent_count": 0}


@pytest.mark.parametrize("changed", [
    {"occurred_at": "2026-09-08T11:05:00+00:00"},
    {"direction": "outbound"},
    {"opportunity_id": "website"},
])
def test_oracle_rejects_changed_second_page_facts(simulation, changed):
    from datetime import datetime

    rows = []
    for identity, contact in (("website", "avery"), ("support", "blake")):
        rows.extend(simulation.fixture_mapping(simulation.source_page(identity, contact))["activities"])
    for row in rows:
        row["occurred_at"] = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))
    assert simulation.correspondence_matches(rows, ("website", "support"))
    target = next(row for row in rows if row["id"] == "simulation-mail:support-reply")
    target.update(changed)
    if isinstance(target["occurred_at"], str):
        target["occurred_at"] = datetime.fromisoformat(target["occurred_at"])
    assert not simulation.correspondence_matches(rows, ("website", "support"))


@pytest.mark.parametrize("changed", [
    {"contact_email": "unrelated@example.com"},
    {"contact_key": "unrelated"},
    {"status": "lost"},
    {"contact_name": "Fabricated Person"},
    {"owner_name": "Fabricated Owner"},
    {"id": "fabricated-opportunity"},
])
def test_catalog_oracle_rejects_changed_or_fabricated_snapshots(simulation, changed):
    pages = [simulation.source_page("website", "avery"), simulation.source_page("support", "blake")]
    rows = [dict(row) for page in pages for row in page["known_opportunities"]]
    assert simulation.opportunity_catalog_matches(rows, pages)
    if "id" in changed:
        rows.append(rows[1] | changed)
    else:
        rows[1].update(changed)
    assert not simulation.opportunity_catalog_matches(rows, pages)
