"""An external sender reports confirmed sends without receiving review authority."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from respawned.api import app as api_module, outbox as outbox_api, setup, ui
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.contracts import OutboxReceiptIn
from respawned.core.delivery import (
    OutboxReceiptConflictError, receipt_activity_id, record_outbox_receipt,
)
from respawned.core.policy import load_policy
from respawned.db.helpers.pg_connect import create_tables
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
OPERATOR = {"Authorization": "Bearer outbox-test-operator"}
CONNECTOR = {"Authorization": "Bearer outbox-test-connector"}
RECEIPT = {"sender": "mail:account-one", "provider_message_id": "message-one",
           "sent_at": (NOW + timedelta(minutes=1)).isoformat()}


@pytest.fixture
def outbox_state(postgres_engine, monkeypatch):
    # Separate committed schemas support concurrent requests and real commits,
    # while the session fixture owns the disposable Docker database.
    schema = "respawned_receipt_" + uuid4().hex
    engine = create_engine(postgres_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    previous = dict(api_module.app.dependency_overrides)
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "outbox-test-operator")
    monkeypatch.setenv("RESPAWNED_OUTBOX_TOKEN", "outbox-test-connector")
    state = SimpleNamespace(engine=engine, now=NOW, calls=0)

    def completion(**_kwargs):
        state.calls += 1
        return {"choices": [{"message": {"content": "Hi Avery, when would you like to speak?"}}]}

    adapter = LiteLLMAdapter("http://unused.test", "unused", "stub", completion_fn=completion)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        create_tables(engine)
        api_module.app.dependency_overrides[api_module.get_api_engine] = lambda: engine
        api_module.app.dependency_overrides[api_module.get_workflow_policy] = lambda: load_policy(DEFAULT_POLICY_PATH)
        api_module.app.dependency_overrides[api_module.get_workflow_clock] = lambda: lambda: state.now
        api_module.app.dependency_overrides[ui.get_draft_adapter_factory] = lambda: lambda: adapter
        with TestClient(api_module.app, raise_server_exceptions=False) as client:
            state.client = client
            yield state
    finally:
        api_module.app.dependency_overrides.clear()
        api_module.app.dependency_overrides.update(previous)
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def _approve(state, key="one", grouped=False):
    ids = [key, key + ":related"] if grouped else [key]
    response = state.client.post("/v1/ui/import", headers=OPERATOR, json={
        "opportunities": [{
            "id": record_id, "contact_key": f"contact:{key}", "contact_name": "Avery",
            "contact_email": f"{key}@example.com", "status": "open",
            "created_at": (NOW - timedelta(days=5)).isoformat(),
        } for record_id in ids],
        "activities": [{
            "id": record_id + ":reply", "opportunity_id": record_id, "type": "contact_replied",
            "direction": "inbound", "channel": "email", "classification": "human",
            "occurred_at": (NOW - timedelta(hours=1)).isoformat(),
        } for record_id in ids],
    })
    assert response.status_code == 200, response.text
    response = state.client.post(f"/v1/ui/records/{key}/draft", headers=OPERATOR, json={})
    assert response.status_code == 200, response.text
    draft = response.json()
    response = state.client.post(f"/v1/ui/drafts/{draft['id']}/approve", headers=OPERATOR,
                                 json={"review_token": draft["review_token"]})
    assert response.status_code == 200, response.text
    return response.json()["outbox_id"]


def _receipt(state, outbox_id, payload=None, headers=CONNECTOR):
    return state.client.post(f"/v1/outbox/{outbox_id}/receipt", headers=headers,
                             json=RECEIPT if payload is None else payload)


@pytest.mark.parametrize("method,path", [
    ("GET", "/v1/outbox/pending"), ("GET", "/v1/outbox/1"),
    ("POST", "/v1/outbox/1/receipt"),
])
def test_connector_authorization_precedes_database(monkeypatch, method, path):
    monkeypatch.setenv("RESPAWNED_OUTBOX_TOKEN", "outbox-test-connector")
    monkeypatch.delenv("RESPAWNED_REVIEW_TOKEN", raising=False)

    def forbidden():
        pytest.fail("Unauthorized connector request opened the database")

    monkeypatch.setitem(api_module.app.dependency_overrides, api_module.get_api_engine, forbidden)
    with TestClient(api_module.app) as client:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic wrong"}):
            response = client.request(method, path, headers=headers, json=RECEIPT if method == "POST" else None)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
            assert response.headers["cache-control"] == "no-store"
        monkeypatch.delenv("RESPAWNED_OUTBOX_TOKEN")
        assert client.request(method, path, headers=CONNECTOR).status_code == 404


def test_connector_token_cannot_review_configure_import_or_process(monkeypatch):
    monkeypatch.setenv("RESPAWNED_OUTBOX_TOKEN", "outbox-test-connector")
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "outbox-test-operator")
    monkeypatch.setenv("RESPAWNED_PROCESS_TOKEN", "outbox-test-process")
    monkeypatch.setitem(api_module.app.dependency_overrides, api_module.get_api_engine,
                        lambda: pytest.fail("Connector token accessed an operator database dependency"))
    with TestClient(api_module.app) as client:
        for method, path in (
            ("GET", "/v1/ui/setup"), ("POST", "/v1/ui/import"),
            ("POST", f"/v1/ui/drafts/{uuid4()}/approve"), ("POST", "/v1/process"),
        ):
            assert client.request(method, path, headers=CONNECTOR, json={}).status_code == 401


def test_pending_feed_detail_export_and_scoped_access(outbox_state, monkeypatch):
    state = outbox_state
    ids = [_approve(state, key) for key in ("one", "two", "legacy")]
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE outbox SET authorization_mode = 'legacy_unknown' WHERE id = :id"),
                           {"id": ids[2]})
        connection.execute(text("UPDATE outbox SET authorization_mode = 'automatic' WHERE id = :id"),
                           {"id": ids[1]})
    for headers in (CONNECTOR, OPERATOR):
        response = state.client.get("/v1/outbox/pending?limit=1", headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert [item["id"] for item in response.json()["items"]] == [ids[0]]
        assert response.json()["has_more"] is True
        detail = state.client.get(f"/v1/outbox/{ids[0]}", headers=headers).json()
        assert detail["receipt"] is None and detail["authorization_mode"] == "human"
        assert detail["contact_address"] == "one@example.com"
    assert len(state.client.get("/v1/outbox").json()["items"]) == 3  # Legacy API preserved.
    assert state.client.get("/v1/outbox/export", headers=OPERATOR).status_code == 200
    assert state.client.get("/v1/outbox/export", headers=CONNECTOR).status_code == 401
    assert state.client.get("/v1/outbox/pending", headers=CONNECTOR).json()["items"][1]["authorization_mode"] == "automatic"
    for limit in (0, 201, -1):
        assert state.client.get(f"/v1/outbox/pending?limit={limit}", headers=CONNECTOR).status_code == 422
    monkeypatch.delenv("RESPAWNED_REVIEW_TOKEN")
    assert state.client.get("/v1/outbox/pending", headers=CONNECTOR).status_code == 200
    state.now = NOW + timedelta(minutes=2)
    assert _receipt(state, ids[2]).status_code == 409


def test_setup_reports_connector_capabilities_without_exposing_credentials(outbox_state, monkeypatch):
    monkeypatch.setattr(setup, "probe_setup_database", lambda: None)
    response = outbox_state.client.get("/v1/ui/setup", headers=OPERATOR)
    assert response.status_code == 200
    assert response.json()["outbox"] == {
        "mode": "api_and_export", "automatic_delivery": False,
        "export_url": "/v1/ui/outbox/export", "pending_url": "/v1/outbox/pending",
        "receipt_url": "/v1/outbox/{id}/receipt", "token_env": "RESPAWNED_OUTBOX_TOKEN",
        "token_configured": True,
    }
    assert "outbox-test-connector" not in response.text and "outbox-test-operator" not in response.text
    monkeypatch.delenv("RESPAWNED_OUTBOX_TOKEN")
    assert outbox_state.client.get("/v1/ui/setup", headers=OPERATOR).json()["outbox"]["token_configured"] is False


@pytest.mark.parametrize("status", ["sent", "failed"])
def test_existing_terminal_status_without_receipt_is_not_overwritten(outbox_state, status):
    state = outbox_state
    outbox_id = _approve(state)
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE outbox SET status = :status WHERE id = :id"),
                           {"id": outbox_id, "status": status})
    state.now = NOW + timedelta(minutes=2)
    assert _receipt(state, outbox_id).status_code == 409
    assert state.client.get(f"/v1/outbox/{outbox_id}", headers=CONNECTOR).json()["status"] == status


def test_grouped_receipt_is_atomic_and_replayable_after_a_lost_response(outbox_state):
    state = outbox_state
    outbox_id = _approve(state, grouped=True)
    before = state.client.get(f"/v1/outbox/{outbox_id}", headers=CONNECTOR).json()
    assert set(before["opportunity_ids"]) == {"one", "one:related"}
    assert state.client.get("/v1/ui/inbox", headers=OPERATOR).json()["total"] == 1
    state.now = NOW + timedelta(minutes=2)
    first = _receipt(state, outbox_id)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["status"] == "sent" and result["receipt"]["sender"] == RECEIPT["sender"]
    assert result["authorization_mode"] == "human"
    for field in ("body", "contact_address", "contact_key", "channel", "opportunity_ids"):
        assert result[field] == before[field]
    assert state.client.get("/v1/outbox/pending", headers=CONNECTOR).json() == {"items": [], "has_more": False}
    assert state.client.get("/v1/ui/inbox", headers=OPERATOR).json()["total"] == 0
    state.now += timedelta(hours=1)
    equivalent_time = (NOW + timedelta(minutes=1)).astimezone(timezone(timedelta(hours=2))).isoformat()
    retried = _receipt(state, outbox_id, {**RECEIPT, "sent_at": equivalent_time})
    assert retried.status_code == 200 and retried.json() == result
    state.now = NOW - timedelta(days=1)  # An existing receipt survives a backward server clock.
    assert _receipt(state, outbox_id).json() == result
    assert state.client.get(f"/v1/outbox/{outbox_id}", headers=CONNECTOR).json() == result
    with state.engine.connect() as connection:
        rows = connection.execute(text("SELECT * FROM activities WHERE type = 'message_sent' ORDER BY opportunity_id")).mappings().all()
        assert {row["opportunity_id"] for row in rows} == {"one", "one:related"}
        assert all(row["channel"] == "email" and row["direction"] == "outbound" for row in rows)
        assert all(row["occurred_at"] == NOW + timedelta(minutes=1) for row in rows)
        assert connection.execute(text("SELECT count(*) FROM outbox_receipts")).scalar_one() == 1
    assert state.calls == 1


def test_receipt_updates_contact_cooldown_after_reservation_cooldown_expires(outbox_state):
    state = outbox_state
    outbox_id = _approve(state)
    state.now = NOW + timedelta(hours=73)
    response = _receipt(state, outbox_id, {**RECEIPT, "sent_at": (NOW + timedelta(hours=71)).isoformat()})
    assert response.status_code == 200, response.text
    response = state.client.post("/v1/ui/import", headers=OPERATOR, json={"activities": [{
        "id": "later-reply", "opportunity_id": "one", "type": "contact_replied",
        "direction": "inbound", "channel": "email", "classification": "human",
        "occurred_at": (NOW + timedelta(hours=72)).isoformat(),
    }]})
    assert response.status_code == 200
    assert state.client.get("/v1/ui/inbox", headers=OPERATOR).json()["total"] == 1
    response = state.client.post("/v1/ui/sync", headers=OPERATOR, json={})
    assert response.status_code == 200 and response.json()["candidate_count"] == 0


def test_pending_poll_restarts_after_acknowledging_an_earlier_batch(outbox_state):
    state = outbox_state
    ids = [_approve(state, key) for key in ("one", "two")]
    state.now = NOW + timedelta(minutes=2)
    assert _receipt(state, ids[0]).status_code == 200
    page = state.client.get("/v1/outbox/pending?limit=1", headers=CONNECTOR).json()
    assert [item["id"] for item in page["items"]] == [ids[1]] and page["has_more"] is False
    for payload in ({**RECEIPT, "provider_message_id": "different"},
                    {**RECEIPT, "sender": "different-account"},
                    {**RECEIPT, "sent_at": (NOW + timedelta(seconds=1)).isoformat()}):
        assert _receipt(state, ids[0], payload).status_code == 409
    assert _receipt(state, ids[1]).status_code == 409  # Same sender message cannot acknowledge another row.
    assert state.client.get(f"/v1/outbox/{ids[1]}", headers=CONNECTOR).json()["status"] == "pending"
    assert _receipt(state, ids[1], {**RECEIPT, "sender": "mail:another-account"}).status_code == 200


@pytest.mark.parametrize("change", [
    {"sender": ""}, {"sender": "account with spaces"}, {"provider_message_id": ""},
    {"provider_message_id": "a" * 301}, {"provider_message_id": "bad\nmessage"},
    {"sent_at": "2026-09-09T12:01:00"}, {"sent_at": "not-a-time"},
    {"sent_at": (NOW - timedelta(seconds=1)).isoformat()},
    {"sent_at": (NOW + timedelta(days=1)).isoformat()}, {"body": "Unapproved replacement"},
    {"contact_address": "replacement@example.com"}, {"status": "failed"},
])
def test_receipt_rejects_invalid_or_unapproved_fields(outbox_state, change):
    state = outbox_state
    outbox_id = _approve(state)
    state.now = NOW + timedelta(minutes=2)
    response = _receipt(state, outbox_id, {**RECEIPT, **change})
    assert response.status_code == 422, response.text
    assert state.client.get(f"/v1/outbox/{outbox_id}", headers=CONNECTOR).json()["receipt"] is None


def test_unknown_receipts_and_current_route_changes(outbox_state):
    state = outbox_state
    assert state.client.get("/v1/outbox/999999", headers=CONNECTOR).status_code == 404
    assert _receipt(state, 999999).status_code == 404
    assert state.client.get("/v1/outbox/0", headers=CONNECTOR).status_code == 422
    assert state.client.get("/v1/outbox/9223372036854775808", headers=CONNECTOR).status_code == 422
    assert _receipt(state, 9223372036854775808).status_code == 422
    outbox_id = _approve(state)
    with state.engine.begin() as connection:
        connection.execute(text("""
            UPDATE opportunities SET contact_key = 'changed-person', contact_email = 'changed@example.com',
                                     status = 'won' WHERE id = 'one'
        """))
    state.now = NOW + timedelta(minutes=2)
    response = _receipt(state, outbox_id)
    assert response.status_code == 200, response.text
    assert response.json()["contact_address"] == "one@example.com"
    assert response.json()["contact_key"] == "contact:one"


def test_conflicting_activity_rolls_back_receipt_even_if_core_caller_catches_error(outbox_state):
    state = outbox_state
    outbox_id = _approve(state, grouped=True)
    item = state.client.get(f"/v1/outbox/{outbox_id}", headers=CONNECTOR).json()
    with state.engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO activities (id, opportunity_id, type, occurred_at)
            VALUES (:id, 'one:related', 'conflicting-source-event', :at)
        """), {"id": receipt_activity_id(item["draft_id"], "one:related"), "at": NOW})
    with state.engine.begin() as connection:
        with pytest.raises(OutboxReceiptConflictError, match="Outbound evidence conflicts"):
            record_outbox_receipt(connection, outbox_id=outbox_id,
                                  receipt=OutboxReceiptIn(**RECEIPT), now=NOW + timedelta(minutes=2))
        assert connection.execute(text("SELECT count(*) FROM outbox_receipts")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM activities WHERE type = 'message_sent'")).scalar_one() == 0
        assert connection.execute(text("SELECT status FROM outbox WHERE id = :id"), {"id": outbox_id}).scalar_one() == "pending"
    state.now = NOW + timedelta(minutes=2)
    assert _receipt(state, outbox_id).status_code == 409


@pytest.mark.parametrize("same_result", [True, False])
def test_concurrent_acknowledgements_serialize_one_reservation(outbox_state, same_result):
    state = outbox_state
    outbox_id = _approve(state, grouped=True)
    state.now = NOW + timedelta(minutes=2)
    barrier = Barrier(2)

    def report(index):
        barrier.wait(timeout=10)
        payload = RECEIPT if same_result or index == 0 else {**RECEIPT, "provider_message_id": "other-result"}
        return _receipt(state, outbox_id, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(report, (0, 1)))
    assert sorted(response.status_code for response in responses) == ([200, 200] if same_result else [200, 409])
    if same_result:
        assert responses[0].json() == responses[1].json()
    with state.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM outbox_receipts")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM activities WHERE type = 'message_sent'")).scalar_one() == 2


def test_concurrent_provider_identity_cannot_acknowledge_two_reservations(outbox_state):
    state = outbox_state
    ids = [_approve(state, key) for key in ("one", "two")]
    state.now = NOW + timedelta(minutes=2)
    barrier = Barrier(2)

    def report(outbox_id):
        barrier.wait(timeout=10)
        return _receipt(state, outbox_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(report, ids))
    assert sorted(response.status_code for response in responses) == [200, 409]
    with state.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM outbox_receipts")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM outbox WHERE status = 'pending'")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM activities WHERE type = 'message_sent'")).scalar_one() == 1


def test_receipt_http_success_waits_for_commit(outbox_state, monkeypatch):
    state = outbox_state
    outbox_id = _approve(state)
    item = state.client.get(f"/v1/outbox/{outbox_id}", headers=CONNECTOR).json()

    @contextmanager
    def transaction():
        yield object()
        raise SQLAlchemyError("private commit diagnostics")

    # Restore this override before the state fixture restores its own overrides.
    with monkeypatch.context() as patch:
        patch.setitem(api_module.app.dependency_overrides, api_module.get_api_engine,
                      lambda: SimpleNamespace(begin=transaction))
        patch.setattr(outbox_api, "record_outbox_receipt", lambda *_args, **_kwargs: item)
        response = _receipt(state, outbox_id)
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_schema_initialization_preserves_existing_receipts(outbox_state):
    state = outbox_state
    outbox_id = _approve(state)
    state.now = NOW + timedelta(minutes=2)
    before = _receipt(state, outbox_id).json()
    create_tables(state.engine)
    assert state.client.get(f"/v1/outbox/{outbox_id}", headers=CONNECTOR).json() == before
