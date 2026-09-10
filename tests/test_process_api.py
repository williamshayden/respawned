"""Operator processing and external reads preserve authorization boundaries."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from respawned.api.app import (
    app, get_api_engine, get_connection, get_workflow_adapter,
    get_workflow_clock, get_workflow_policy,
)
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.ingest import ingest_records
from respawned.core.policy import ReviewPolicy, load_policy
from respawned.core.workflow import process_candidates
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
TOKEN = "simulation-operator-credential"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _records(key="one", **values):
    return {
        "opportunities": [{
            "id": key, "contact_key": f"crm:{key}", "contact_name": "Avery",
            "contact_email": f"{key}@example.com", "status": "open",
            "created_at": (NOW - timedelta(days=3)).isoformat(), **values,
        }],
        "activities": [{
            "id": f"{key}:reply", "opportunity_id": key,
            "type": "contact_replied", "direction": "inbound", "channel": "email",
            "occurred_at": (NOW - timedelta(hours=1)).isoformat(),
        }],
    }


@pytest.fixture
def isolated_dependencies():
    previous = dict(app.dependency_overrides)
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("credential", [None, "wrong", ""])
def test_processing_disabled_or_unauthorized_is_inert(
    monkeypatch, isolated_dependencies, credential,
):
    for dependency in (get_api_engine, get_workflow_adapter, get_workflow_policy):
        app.dependency_overrides[dependency] = lambda: pytest.fail("resolved workflow dependency")
    monkeypatch.delenv("RESPAWNED_PROCESS_TOKEN", raising=False)
    monkeypatch.delenv("RESPAWNED_REVIEW_TOKEN", raising=False)
    with TestClient(app) as client:
        assert client.post("/v1/process", json={}).status_code == 404
        monkeypatch.setenv("RESPAWNED_PROCESS_TOKEN", TOKEN)
        headers = {} if credential is None else {"Authorization": f"Bearer {credential}"}
        response = client.post("/v1/process", json={}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.fixture
def workflow_api(postgres_connection, monkeypatch, isolated_dependencies):
    connection = postgres_connection
    state = SimpleNamespace(policy=load_policy(DEFAULT_POLICY_PATH), calls=[], commit_failure=None,
                            transaction_count=0, clock=lambda: NOW)

    @contextmanager
    def begin():
        state.transaction_count += 1
        transaction_number = state.transaction_count
        with connection.begin_nested():
            yield connection
            if transaction_number == state.commit_failure:
                raise RuntimeError("simulated commit failure")

    def completion(**kwargs):
        state.calls.append(kwargs)
        return {"choices": [{"message": {"content": "Hi Avery, thanks for your reply. How can I help?"}}]}

    state.adapter = LiteLLMAdapter("http://unused.test", "unused", "scripted", completion_fn=completion)
    state.engine = SimpleNamespace(begin=begin)
    app.dependency_overrides[get_connection] = lambda: connection
    app.dependency_overrides[get_api_engine] = lambda: state.engine
    app.dependency_overrides[get_workflow_policy] = lambda: state.policy
    app.dependency_overrides[get_workflow_adapter] = lambda: state.adapter
    app.dependency_overrides[get_workflow_clock] = lambda: state.clock
    monkeypatch.setenv("RESPAWNED_PROCESS_TOKEN", TOKEN)
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "workflow-test-operator")
    with TestClient(app, raise_server_exceptions=False,
                    headers={"Authorization": "Bearer workflow-test-operator"}) as client:
        state.client = client
        yield state


@pytest.mark.parametrize("payload", [
    {"mode": "automatic"}, {"review": {"mode": "automatic"}},
    {"now": NOW.isoformat()}, {"approved": True}, {"actor": "human"},
    {"limit": 0}, {"limit": 51}, {"limit": True}, {"limit": 1.5},
])
def test_process_cannot_override_operator_policy_clock_or_bounds(workflow_api, payload):
    state = workflow_api
    response = state.client.post("/v1/process", headers=HEADERS, json=payload)
    assert response.status_code == 422
    assert state.calls == []
    assert state.transaction_count == 0


def _process(state, **payload):
    response = state.client.post("/v1/process", headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_human_mode_replay_and_server_toggle_preserve_provenance(workflow_api):
    state = workflow_api
    client = state.client
    assert client.post("/v1/ingest", json=_records()).status_code == 200
    first = _process(state)
    repeated = _process(state)
    assert first == repeated
    assert first["review_mode"] == "human"
    assert first["items"][0]["status"] == "pending"
    assert len(state.calls) == 1
    assert client.get("/v1/outbox").json()["items"] == []

    state.policy = replace(state.policy, review=ReviewPolicy("automatic"))
    automatic = _process(state)
    assert automatic["review_mode"] == "automatic"
    assert automatic["items"][0]["status"] == "authorized"
    assert len(state.calls) == 1
    row = client.get("/v1/outbox").json()["items"][0]
    assert row["id"] == automatic["items"][0]["outbox_id"]
    assert row["draft_id"] == first["items"][0]["draft_id"]
    assert row["authorization_mode"] == "automatic"
    assert row["status"] == "pending" and row["sent_at"] is None
    assert _process(state)["items"] == []
    assert len(client.get("/v1/outbox").json()["items"]) == 1

    state.policy = replace(state.policy, review=ReviewPolicy("human"))
    assert client.post("/v1/ingest", json=_records("two")).status_code == 200
    assert _process(state)["items"][0]["status"] == "pending"
    drafts = client.get("/v1/drafts").json()["items"]
    assert len(drafts) == 2
    assert all(draft["reviewed_at"] is None for draft in drafts)
    assert client.get("/v1/outbox").json()["items"][0]["authorization_mode"] == "automatic"
    assert client.get("/v1/drafts?limit=1").json()["has_more"]
    assert len(client.get("/v1/drafts?limit=1&offset=1").json()["items"]) == 1


def test_process_blocks_model_failure_and_recovers(workflow_api):
    state = workflow_api
    state.policy = replace(state.policy, review=ReviewPolicy("automatic"))
    assert state.client.post("/v1/ingest", json=_records()).status_code == 200
    valid_adapter = state.adapter
    state.adapter = replace(state.adapter, completion_fn=lambda **kwargs: {"choices": []})
    failure = _process(state)
    assert failure["items"][0]["status"] == "blocked"
    assert "generation failed" in failure["items"][0]["detail"]
    assert state.client.get("/v1/drafts").json()["items"] == []
    assert state.client.get("/v1/outbox").json()["items"] == []
    state.adapter = valid_adapter
    assert _process(state)["items"][0]["status"] == "authorized"


def test_automatic_process_rechecks_state_after_drafting(workflow_api, postgres_connection):
    state = workflow_api
    state.policy = replace(state.policy, review=ReviewPolicy("automatic"))
    assert state.client.post("/v1/ingest", json=_records()).status_code == 200
    calls = []

    def clock():
        calls.append(NOW)
        if len(calls) == 3:
            ingest_records(postgres_connection, **_records(status="lost"))
        return NOW

    state.clock = clock
    result = _process(state)
    assert result["items"][0]["status"] == "blocked"
    assert "no longer open/contactable" in result["items"][0]["detail"]
    assert state.client.get("/v1/outbox").json()["items"] == []
    assert state.client.get("/v1/drafts").json()["items"][0]["status"] == "pending"


def test_process_does_not_acknowledge_failed_authorization_commit(workflow_api):
    state = workflow_api
    state.policy = replace(state.policy, review=ReviewPolicy("automatic"))
    assert state.client.post("/v1/ingest", json=_records()).status_code == 200
    state.commit_failure = 3
    response = state.client.post("/v1/process", headers=HEADERS, json={})
    assert response.status_code == 500
    assert state.client.get("/v1/outbox").json()["items"] == []
    assert state.client.get("/v1/drafts").json()["items"][0]["status"] == "pending"
    state.commit_failure = None
    assert _process(state)["items"][0]["status"] == "authorized"
    assert len(state.calls) == 1


def test_outbox_pagination_is_read_only(workflow_api):
    state = workflow_api
    state.policy = replace(state.policy, review=ReviewPolicy("automatic"))
    for key in ("one", "two"):
        assert state.client.post("/v1/ingest", json=_records(key)).status_code == 200
    result = _process(state, limit=1)
    assert result["candidate_count"] == 1
    assert len(state.calls) == 1
    assert _process(state)["candidate_count"] == 1
    first = state.client.get("/v1/outbox?limit=1").json()
    second = state.client.get("/v1/outbox?limit=1&offset=1").json()
    assert first["has_more"] and not second["has_more"]
    assert first["items"][0]["id"] < second["items"][0]["id"]
    assert len(state.client.get("/v1/outbox").json()["items"]) == 2
    assert all(row["sent_at"] is None for row in first["items"] + second["items"])
    for path in ("/v1/drafts", "/v1/outbox"):
        assert state.client.get(f"{path}?limit=201").status_code == 422
        assert state.client.get(f"{path}?offset=-1").status_code == 422


def test_explicit_workflow_review_stays_human_with_automatic_processing_policy(workflow_api, postgres_connection):
    state = workflow_api
    state.policy = replace(state.policy, review=ReviewPolicy("automatic"))
    ingest_records(postgres_connection, **_records())
    draft_response = state.client.post("/v1/workflow/records/one/draft", json={
        "body": "Hi Avery, thanks for your reply. When would you like to speak?",
    })
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()
    approved = state.client.post(f"/v1/workflow/drafts/{draft['id']}/approve", json={
        "review_token": draft["review_token"],
    })
    assert approved.status_code == 200, approved.text
    row = postgres_connection.execute(text("SELECT authorization_mode FROM outbox")).scalar_one()
    assert row == "human" and state.calls == []


@pytest.mark.parametrize("limit", [0, 51, True, 1.5])
def test_process_service_rejects_unbounded_work_before_dependencies(limit):
    with pytest.raises(ValueError, match="between 1 and 50"):
        process_candidates(None, policy=None, adapter=None, limit=limit)


def test_operator_can_process_without_a_separate_process_secret(workflow_api, monkeypatch):
    state = workflow_api
    monkeypatch.delenv("RESPAWNED_PROCESS_TOKEN")
    response = state.client.post("/v1/process", json={})
    assert response.status_code == 200, response.text
    transactions = state.transaction_count
    monkeypatch.setenv("RESPAWNED_OUTBOX_TOKEN", "delivery-only")
    denied = state.client.post("/v1/process", json={}, headers={"Authorization": "Bearer delivery-only"})
    assert denied.status_code == 401
    assert state.transaction_count == transactions and state.calls == []


def test_local_cli_capability_can_process_without_browser_cookie(workflow_api, monkeypatch):
    from respawned.api.session import LocalSession

    state = workflow_api
    manager = LocalSession(8123)
    monkeypatch.setattr(app.state, "local_session", manager, raising=False)
    monkeypatch.delenv("RESPAWNED_PROCESS_TOKEN")
    monkeypatch.delenv("RESPAWNED_REVIEW_TOKEN")
    with TestClient(app, base_url=manager.origin) as client:
        response = client.post("/v1/process", json={}, headers={"Authorization": f"Bearer {manager.cli_token}"})
        assert response.status_code == 200, response.text
        assert state.calls == []
