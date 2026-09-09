"""Workspace CRUD is authenticated, durable, and independent of tracked records."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from respawned.api.app import app, get_api_engine, get_connection, get_workflow_clock
from respawned.core.ingest import ingest_records
from respawned.db.helpers.pg_connect import create_tables


TOKEN = "workspace-review-test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
PREFIX = "/v1/ui/workspaces"
NOW = datetime(2026, 9, 9, tzinfo=UTC)


@pytest.fixture
def dependencies(monkeypatch):
    previous = dict(app.dependency_overrides)
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", TOKEN)
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("method,path", [
    ("get", ""), ("post", ""),
    ("put", "/00000000-0000-0000-0000-000000000001"),
    ("delete", "/00000000-0000-0000-0000-000000000001"),
])
def test_authorization_precedes_database(dependencies, monkeypatch, method, path):
    def forbidden():
        pytest.fail("resolved the database before reviewer authorization")

    for dependency in (get_api_engine, get_connection):
        app.dependency_overrides[dependency] = forbidden
    monkeypatch.setenv("RESPAWNED_PROCESS_TOKEN", "process-only")
    with TestClient(app) as client:
        request = getattr(client, method)
        assert request(PREFIX + path).status_code == 401
        assert request(PREFIX + path, headers={
            "Authorization": "Bearer process-only",
        }).status_code == 401
        monkeypatch.delenv("RESPAWNED_REVIEW_TOKEN")
        assert request(PREFIX + path, headers=HEADERS).status_code == 404


@pytest.fixture
def workspace_api(postgres_connection, dependencies):
    @contextmanager
    def begin():
        with postgres_connection.begin_nested():
            yield postgres_connection

    app.dependency_overrides[get_api_engine] = lambda: SimpleNamespace(begin=begin)
    app.dependency_overrides[get_workflow_clock] = lambda: lambda: NOW
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, connection=postgres_connection)


def test_workspace_crud_and_unpaginated_kind_discovery_preserve_records(workspace_api):
    state = workspace_api
    # More kinds than the initial review queue page, including closed/contactless rows.
    records = [{
        "id": f"record-{index}", "kind": f"kind_{index:03}",
        "status": "lost" if index % 2 else "open",
        "created_at": datetime(2026, 9, 9, tzinfo=UTC),
    } for index in range(56)]
    ingest_records(state.connection, opportunities=records)
    initial = state.client.get(PREFIX, headers=HEADERS).json()
    assert initial == {"items": [], "available_kinds": [item["kind"] for item in records],
                       "scope": "shared_engine"}
    response = state.client.post(PREFIX, headers=HEADERS, json={
        "name": "  Research outreach  ", "description": "Partners and research teams",
        "kinds": ["kind_000", "future_type"],
    })
    assert response.status_code == 201, response.text
    created = response.json()
    assert UUID(created["id"])
    assert created["name"] == "Research outreach"
    assert created["description"] == "Partners and research teams"
    assert created["kinds"] == ["kind_000", "future_type"]
    assert created["created_at"] == created["updated_at"]
    path = f"{PREFIX}/{created['id']}"
    updated = state.client.put(path, headers=HEADERS, json={"name": "Everything"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["kinds"] == []
    assert updated.json()["description"] == ""
    assert updated.json()["id"] == created["id"]
    assert updated.json()["created_at"] == created["created_at"]
    assert updated.json()["updated_at"] >= created["updated_at"]
    assert state.client.get(PREFIX, headers=HEADERS).json()["items"] == [updated.json()]
    deleted = state.client.delete(path, headers=HEADERS)
    assert deleted.status_code == 204 and deleted.content == b""
    assert state.client.get(PREFIX, headers=HEADERS).json()["items"] == []
    assert state.connection.execute(text("SELECT count(*) FROM opportunities")).scalar_one() == 56
    assert state.client.delete(path, headers=HEADERS).status_code == 404
    assert state.client.put(path, headers=HEADERS, json={"name": "Gone"}).status_code == 404


@pytest.mark.parametrize("payload", [
    {}, {"name": "   "}, {"name": "x" * 121}, {"name": 42},
    {"name": "Valid", "description": "x" * 2001},
    {"name": "Valid", "description": None},
    {"name": "Valid", "kinds": ["Invalid kind"]},
    {"name": "Valid", "kinds": ["x" * 65]},
    {"name": "Valid", "kinds": [None]},
    {"name": "Valid", "kinds": ["duplicate", "duplicate"]},
    {"name": "Valid", "kinds": [f"kind_{index}" for index in range(101)]},
    {"name": "Valid", "kinds": "job_application"},
    {"name": "Valid", "policy_mode": "automatic"},
])
def test_bounded_strict_workspace_writes_leave_no_state(workspace_api, payload):
    state = workspace_api
    for method, path in (("post", PREFIX), ("put", f"{PREFIX}/{uuid4()}")):
        response = getattr(state.client, method)(path, headers=HEADERS, json=payload)
        assert response.status_code == 422, response.text
    assert state.connection.execute(text("SELECT count(*) FROM workspaces")).scalar_one() == 0


def test_workspace_filter_precedes_record_pagination_and_all_filter_is_explicit(workspace_api):
    state = workspace_api
    ingest_records(state.connection, opportunities=[{
        "id": f"record-{index:03}", "kind": "late_kind" if index >= 55 else "earlier_kind",
        "created_at": NOW, "status": "open",
    } for index in range(57)])
    saved = state.client.post(PREFIX, headers=HEADERS, json={
        "name": "Later records", "kinds": ["late_kind"],
    }).json()
    query = f"/v1/ui/records?workspace_id={saved['id']}"
    first = state.client.get(query + "&limit=1", headers=HEADERS).json()
    assert first["total"] == 2 and first["has_more"]
    assert [record["id"] for record in first["items"]] == ["record-055"]
    second = state.client.get(query + "&limit=1&offset=1", headers=HEADERS).json()
    assert second["total"] == 2 and not second["has_more"]
    assert [record["id"] for record in second["items"]] == ["record-056"]
    # Direct record targets from related inbox/outbox references remain accessible.
    assert state.client.get("/v1/ui/records/record-000", headers=HEADERS).status_code == 200
    assert state.client.put(f"{PREFIX}/{saved['id']}", headers=HEADERS,
                            json={"name": "All records", "kinds": []}).status_code == 200
    assert state.client.get(query + "&limit=1", headers=HEADERS).json()["total"] == 57
    assert state.client.get(f"/v1/ui/records?workspace_id={uuid4()}", headers=HEADERS).status_code == 404
    assert state.client.get("/v1/ui/records?workspace_id=invalid", headers=HEADERS).status_code == 422


def test_workspace_filter_preserves_outbound_cooldown_from_another_kind(workspace_api):
    state = workspace_api
    contact = {"contact_key": "one-person", "contact_email": "person@example.com", "status": "open"}
    ingest_records(state.connection, opportunities=[{
        "id": "selected-project", "kind": "project", "created_at": NOW - timedelta(days=20),
        **contact,
    }])
    saved = state.client.post(PREFIX, headers=HEADERS, json={
        "name": "Projects", "kinds": ["project"],
    }).json()
    query = f"/v1/ui/records?workspace_id={saved['id']}"
    assert state.client.get(query, headers=HEADERS).json()["items"][0]["next_action"] == "follow_up"
    ingest_records(state.connection, opportunities=[{
        "id": "unselected-ticket", "kind": "support_ticket", "created_at": NOW - timedelta(days=2),
        "last_contact_at": NOW - timedelta(hours=1), **contact,
    }])
    filtered = state.client.get(query, headers=HEADERS).json()
    assert filtered["total"] == 1
    visible = filtered["items"][0]
    assert visible["candidate_id"] is None and visible["score"] is None
    assert visible["next_action"] == "waiting" and visible["reason"]["code"] == "cooldown"
    global_records = state.client.get("/v1/ui/records", headers=HEADERS).json()["items"]
    assert visible == next(record for record in global_records if record["id"] == "selected-project")


def test_workspace_inbox_finds_matching_route_beyond_global_limit(workspace_api):
    state = workspace_api
    records = [{
        "id": f"record-{index:03}", "kind": "project" if index == 200 else "support_ticket",
        "contact_key": f"person-{index:03}", "contact_email": f"person-{index}@example.com",
        "created_at": NOW - timedelta(days=20), "status": "open",
    } for index in range(201)]
    ingest_records(state.connection, opportunities=records, activities=[{
        "id": f"reply-{index}", "opportunity_id": record["id"], "type": "contact_replied",
        "direction": "inbound", "channel": "email", "classification": "human",
        "occurred_at": NOW - (timedelta(days=1) if index == 200 else timedelta(hours=1)),
    } for index, record in enumerate(records)])
    global_inbox = state.client.get("/v1/ui/inbox", headers=HEADERS).json()
    assert global_inbox["total"] == 201 and global_inbox["has_more"]
    assert len(global_inbox["items"]) == 200
    assert "person-200" not in {item["contact_key"] for item in global_inbox["items"]}
    saved = state.client.post(PREFIX, headers=HEADERS, json={
        "name": "Projects", "kinds": ["project"],
    }).json()
    query = f"/v1/ui/inbox?workspace_id={saved['id']}"
    scoped = state.client.get(query, headers=HEADERS).json()
    assert scoped["total"] == 1 and not scoped["has_more"]
    assert [item["contact_key"] for item in scoped["items"]] == ["person-200"]
    assert scoped["items"][0]["record_refs"] == [
        {"id": "record-200", "kind": "project", "title": "record-200"},
    ]
    assert state.client.put(f"{PREFIX}/{saved['id']}", headers=HEADERS,
                            json={"name": "Everything", "kinds": []}).status_code == 200
    assert state.client.get(query, headers=HEADERS).json() == global_inbox
    assert state.client.get(f"/v1/ui/inbox?workspace_id={uuid4()}", headers=HEADERS).status_code == 404
    assert state.client.get("/v1/ui/inbox?workspace_id=invalid", headers=HEADERS).status_code == 422


def test_workspace_inbox_retains_complete_groups_and_global_outbound_resolution(workspace_api):
    state = workspace_api
    contact = {"contact_key": "same-person", "contact_email": "person@example.com",
               "status": "open", "created_at": NOW - timedelta(days=20)}
    ingest_records(state.connection, opportunities=[
        {"id": "workspace-evidence", "kind": "project", "value": 1, **contact},
        {"id": "other-primary", "kind": "support_ticket", "value": 1000, **contact},
    ], activities=[{
        "id": f"reply-{key}", "opportunity_id": key, "type": "contact_replied",
        "direction": "inbound", "channel": "email", "classification": "human",
        "occurred_at": NOW - timedelta(hours=hours),
    } for key, hours in (("workspace-evidence", 2), ("other-primary", 1))])
    assert state.client.post("/v1/ui/sync", headers=HEADERS, json={}).status_code == 200
    global_item = state.client.get("/v1/ui/inbox", headers=HEADERS).json()["items"][0]
    assert global_item["review_record_id"] == "other-primary"
    saved = state.client.post(PREFIX, headers=HEADERS, json={
        "name": "Projects", "kinds": ["project"],
    }).json()
    query = f"/v1/ui/inbox?workspace_id={saved['id']}"
    scoped = state.client.get(query, headers=HEADERS).json()
    assert scoped["total"] == 1 and scoped["items"] == [global_item]
    assert len(scoped["items"][0]["reply_evidence"]) == 2
    assert set(scoped["items"][0]["opportunity_ids"]) == {"workspace-evidence", "other-primary"}
    # Even a closed record of another type resolves the shared contact's replies.
    ingest_records(state.connection, opportunities=[{
        "id": "other-outbound", "kind": "support_ticket", **contact,
        "status": "lost", "last_contact_at": NOW - timedelta(minutes=30),
    }])
    resolved = state.client.get(query, headers=HEADERS).json()
    assert resolved["items"] == [] and resolved["total"] == 0


def test_workspace_is_committed_before_response_and_survives_schema_reinitialization(
    postgres_engine, dependencies,
):
    app.dependency_overrides[get_api_engine] = lambda: postgres_engine
    workspace_id = None
    try:
        with TestClient(app) as first_client:
            response = first_client.post(PREFIX, headers=HEADERS, json={
                "name": "Long-running projects", "kinds": ["project"],
            })
            assert response.status_code == 201, response.text
            created = response.json()
            workspace_id = created["id"]
            # A separate connection observes the committed HTTP write immediately.
            with postgres_engine.connect() as connection:
                row = connection.execute(text("SELECT name, kinds FROM workspaces WHERE id = :id"),
                                         {"id": workspace_id}).mappings().one()
                assert row["name"] == created["name"] and row["kinds"] == ["project"]
        create_tables(postgres_engine)
        create_tables(postgres_engine)
        with TestClient(app) as reopened:
            assert created in reopened.get(PREFIX, headers=HEADERS).json()["items"]
            assert reopened.delete(f"{PREFIX}/{workspace_id}", headers=HEADERS).status_code == 204
            with postgres_engine.connect() as connection:
                assert connection.execute(text("SELECT 1 FROM workspaces WHERE id = :id"),
                                          {"id": workspace_id}).scalar_one_or_none() is None
    finally:
        if workspace_id is not None:
            with postgres_engine.begin() as connection:
                connection.execute(text("DELETE FROM workspaces WHERE id = :id"), {"id": workspace_id})
