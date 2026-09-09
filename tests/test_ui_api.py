"""The browser is a human reviewer, never an ingestion or processing authority."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from respawned.api import ui
from respawned.api.app import (
    app, get_api_engine, get_connection, get_workflow_clock, get_workflow_policy,
)
from respawned.api.ui_models import UIDraft
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.ingest import ingest_records
from respawned.core.policy import ReviewPolicy, load_policy
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
TOKEN = "review-only-test-credential"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
DRAFT_ID = "00000000-0000-0000-0000-000000000001"


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
    ("get", "/config"), ("get", "/records"), ("get", "/records/one"),
    ("get", "/outbox"), ("get", "/inbox"),
    ("post", "/sync"), ("post", "/records/one/draft"),
    ("post", f"/drafts/{DRAFT_ID}/edit"),
    ("post", f"/drafts/{DRAFT_ID}/approve"),
    ("post", f"/drafts/{DRAFT_ID}/reject"),
])
def test_review_auth_precedes_database_policy_and_provider(
    dependencies, monkeypatch, method, path,
):
    def forbidden():
        pytest.fail("resolved a protected dependency before authorization")

    for dependency in (get_api_engine, get_connection, get_workflow_policy,
                       get_workflow_clock, ui.get_draft_adapter_factory):
        app.dependency_overrides[dependency] = forbidden
    monkeypatch.setenv("RESPAWNED_PROCESS_TOKEN", "processing-token")
    with TestClient(app) as client:
        call = getattr(client, method)
        response = call("/v1/ui" + path)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert call("/v1/ui" + path, headers={"Authorization": "Bearer processing-token"}).status_code == 401
        monkeypatch.delenv("RESPAWNED_REVIEW_TOKEN")
        assert call("/v1/ui" + path, headers=HEADERS).status_code == 404


def _record(key="one", **overrides):
    return {
        "id": key, "contact_key": f"person:{key}", "contact_name": "Avery",
        "contact_email": f"{key}@example.com", "status": "open",
        "created_at": NOW - timedelta(days=5), **overrides,
    }


def _reply(key="one", **overrides):
    return {
        "id": f"{key}:reply", "opportunity_id": key, "type": "contact_replied",
        "direction": "inbound", "channel": "email",
        "occurred_at": NOW - timedelta(hours=1),
        "classification": "human", "summary": "Can we speak this week?",
        "source_url": "https://example.com/thread/one", **overrides,
    }


@pytest.fixture
def review_api(postgres_connection, dependencies):
    state = SimpleNamespace(connection=postgres_connection, calls=[],
                            policy=load_policy(DEFAULT_POLICY_PATH))

    def completion(**kwargs):
        state.calls.append(kwargs)
        return {"choices": [{"message": {"content": "Hi Avery, thanks for your reply. When works for you?"}}]}

    state.adapter = LiteLLMAdapter("http://unused.test", "unused", "stub", completion_fn=completion)

    @contextmanager
    def begin():
        with postgres_connection.begin_nested():
            yield postgres_connection

    app.dependency_overrides[get_api_engine] = lambda: SimpleNamespace(begin=begin)
    app.dependency_overrides[get_workflow_policy] = lambda: state.policy
    app.dependency_overrides[get_workflow_clock] = lambda: lambda: NOW
    app.dependency_overrides[ui.get_draft_adapter_factory] = lambda: lambda: state.adapter
    with TestClient(app, raise_server_exceptions=False) as client:
        state.client = client
        yield state


def _get(state, path):
    response = state.client.get("/v1/ui" + path, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def _post(state, path, payload=None, status=200):
    response = state.client.post("/v1/ui" + path, headers=HEADERS, json=payload or {})
    assert response.status_code == status, response.text
    return response.json()


def _draft(state, key="one"):
    ingest_records(state.connection, opportunities=[_record(key)], activities=[_reply(key)])
    _post(state, "/sync")
    return _post(state, f"/records/{key}/draft")


def test_reads_and_sync_preserve_context_and_contactless_tracking_without_model_calls(review_api):
    state = review_api
    ingest_records(state.connection, opportunities=[
        _record(kind="job_application", title="Platform engineer at Acme", value=99999, context={
            "company": "Acme", "role": "Platform engineer", "stage": "Interview",
            "summary": "A long source summary. " * 50,
            "source_url": "https://example.com/applications/acme",
        }),
        _record("track-only", contact_key=None, contact_email=None, kind="job_application",
                title="Application without a recruiter"),
        _record("closed", status="lost"),
        _record("business", value=1250),
    ], activities=[_reply()])
    before = _get(state, "/records")
    assert before["total"] == 4
    records = {item["id"]: item for item in before["items"]}
    job = records["one"]
    assert job["kind"] == "job_application" and job["stage"] == "Interview"
    assert job["reason"]["code"] == "human_reply" and job["next_action"] == "reply"
    assert job["source_freshness"] == "unknown" and job["candidate_id"] is not None
    assert job["draft"] is None and job["activities"][0]["classification"] == "human"
    assert job["activities"][0]["summary"] == "Can we speak this week?"
    assert "Value" not in {field["label"] for field in job["fields"]}
    assert {field["label"] for field in job["fields"]} == {"Company", "Role"}
    assert job["source_url"] == "https://example.com/applications/acme"
    assert "Value" in {field["label"] for field in records["business"]["fields"]}
    assert records["track-only"]["contact"] is None
    assert records["track-only"]["reason"]["code"] == "missing_contact"
    assert records["closed"]["next_action"] == "closed"
    assert state.connection.execute(text("SELECT count(*) FROM sync_runs")).scalar_one() == 0
    result = _post(state, "/sync")
    assert result["candidate_count"] >= 1
    after = _get(state, "/records?limit=1")
    assert after["has_more"] and after["items"][0]["candidate_id"]
    assert len(_get(state, "/records?limit=1&offset=1")["items"]) == 1
    assert _get(state, "/config")["policy_mode"] == "human"
    assert state.calls == []
    assert state.connection.execute(text("SELECT count(*) FROM drafts")).scalar_one() == 0
    assert _get(state, "/outbox")["items"] == []
    _post(state, "/records/track-only/draft", status=409)
    assert state.calls == []


def test_lazy_draft_edit_stale_approval_and_idempotent_unsent_outbox(review_api):
    state = review_api
    original = _draft(state)
    assert len(state.calls) == 1
    # Existing drafts remain available even after optional drafting is disabled.
    app.dependency_overrides[ui.get_draft_adapter_factory] = lambda: lambda: pytest.fail("loaded provider for existing copy")
    assert _post(state, "/records/one/draft") == original
    edited = _post(state, f"/drafts/{original['id']}/edit", {
        "body": "Hi Avery, Thursday works for me. Would the afternoon suit you?",
        "review_token": original["review_token"],
    })
    assert edited["review_token"] != original["review_token"]
    stale = _post(state, f"/drafts/{original['id']}/approve", {
        "review_token": original["review_token"],
    }, status=409)
    assert "changed since it was shown" in stale["detail"]
    assert _get(state, "/outbox")["items"] == []
    approved = _post(state, f"/drafts/{edited['id']}/approve", {"review_token": edited["review_token"]})
    assert approved["status"] == "approved" and approved["outbox_id"] is not None
    assert _post(state, f"/drafts/{edited['id']}/approve", {"review_token": edited["review_token"]}) == approved
    rows = _get(state, "/outbox")["items"]
    assert len(rows) == 1 and rows[0]["body"] == edited["body"]
    assert rows[0]["authorization_mode"] == "human"
    assert rows[0]["status"] == "pending" and rows[0]["sent_at"] is None
    assert _get(state, "/records")["items"][0]["next_action"] == "approved"
    assert len(state.calls) == 1


@pytest.mark.parametrize("sync_first", [False, True])
def test_selected_lazy_draft_is_independent_of_global_sync_limit(review_api, sync_first):
    state = review_api
    keys = [f"lazy-{index:03}" for index in range(201)]
    ingest_records(state.connection, opportunities=[_record(key) for key in keys],
                   activities=[_reply(key) for key in keys])
    if sync_first:
        assert _post(state, "/sync")["candidate_count"] == 200
    last = _get(state, "/records?limit=1&offset=200")["items"][0]
    assert last["candidate_id"] is not None
    before = set(state.connection.execute(text("SELECT primary_opportunity_id FROM candidates")).scalars())
    assert last["id"] not in before
    before_runs = state.connection.execute(text("SELECT count(*) FROM sync_runs")).scalar_one()
    assert state.client.post(f"/v1/ui/records/{last['id']}/draft").status_code == 401
    assert state.connection.execute(text("SELECT count(*) FROM sync_runs")).scalar_one() == before_runs
    assert state.calls == []
    draft = _post(state, f"/records/{last['id']}/draft")
    assert draft["status"] == "pending" and len(state.calls) == 1
    after = set(state.connection.execute(text("SELECT primary_opportunity_id FROM candidates")).scalars())
    assert after - before == {last["id"]}
    assert state.connection.execute(text("SELECT count(*) FROM sync_runs")).scalar_one() == before_runs + 1
    assert state.connection.execute(text("SELECT count(*) FROM drafts")).scalar_one() == 1
    assert _get(state, "/outbox")["items"] == []


def test_selected_drafting_cannot_promote_an_ineligible_contact_sibling(review_api):
    state = review_api
    ingest_records(state.connection, opportunities=[
        _record(key, contact_key="shared-person", contact_email="shared@example.com", value=value)
        for key, value in (("group-primary", 1000), ("group-sibling", 0))
    ], activities=[_reply("group-primary"), _reply("group-sibling")])
    records = _get(state, "/records")["items"]
    primary = next(item for item in records if item["candidate_id"] is not None)
    sibling = next(item for item in records if item["candidate_id"] is None)
    inbox = _get(state, "/inbox")["items"]
    assert inbox[0]["review_record_id"] == primary["id"]
    _post(state, f"/records/{sibling['id']}/draft", status=409)
    assert state.calls == []
    assert state.connection.execute(text("SELECT count(*) FROM candidates")).scalar_one() == 0
    assert state.connection.execute(text("SELECT count(*) FROM sync_runs")).scalar_one() == 0
    assert _post(state, f"/records/{primary['id']}/draft")["status"] == "pending"
    assert len(state.calls) == 1


def test_rejection_and_validation_never_authorize_a_message(review_api):
    state = review_api
    draft = _draft(state)
    _post(state, f"/drafts/{draft['id']}/edit", {
        "review_token": draft["review_token"], "body": "Hi [name], following up.",
    }, status=422)
    assert _get(state, "/records")["items"][0]["draft"]["body"] == draft["body"]
    rejected = _post(state, f"/drafts/{draft['id']}/reject", {"review_token": draft["review_token"]})
    assert rejected["status"] == "rejected"
    _post(state, f"/drafts/{draft['id']}/approve", {"review_token": draft["review_token"]}, status=409)
    assert _post(state, "/sync")["candidate_count"] == 0
    assert _get(state, "/outbox")["items"] == []


def test_source_record_ids_can_contain_slashes(review_api):
    state = review_api
    key = "ats/application/one"
    ingest_records(state.connection, opportunities=[_record(key)], activities=[_reply(key)])
    _post(state, "/sync")
    draft = _post(state, "/records/ats%2Fapplication%2Fone/draft")
    assert draft["status"] == "pending"
    assert _get(state, "/records/ats%2Fapplication%2Fone")["id"] == key
    assert len(state.calls) == 1


@pytest.mark.parametrize("change", ["closed", "route", "cooldown"])
def test_approval_rechecks_live_lifecycle_route_and_contact_cooldown(review_api, change):
    state = review_api
    draft = _draft(state)
    if change == "closed":
        ingest_records(state.connection, opportunities=[_record(status="lost")])
    elif change == "route":
        ingest_records(state.connection, opportunities=[_record(contact_email="new@example.com")])
    else:
        ingest_records(state.connection, activities=[_reply(
            id="outbound:recent", type="message_sent", direction="outbound",
            occurred_at=NOW - timedelta(minutes=1),
        )])
    _post(state, f"/drafts/{draft['id']}/approve", {"review_token": draft["review_token"]}, status=409)
    assert _get(state, "/outbox")["items"] == []


def test_ui_explicit_approval_remains_human_under_automatic_processing_policy(review_api):
    state = review_api
    state.policy = replace(state.policy, review=ReviewPolicy("automatic"))
    draft = _draft(state)
    assert _get(state, "/config")["policy_mode"] == "automatic"
    assert draft["status"] == "pending"
    assert _get(state, "/outbox")["items"] == []
    _post(state, f"/drafts/{draft['id']}/approve", {"review_token": draft["review_token"]})
    assert _get(state, "/outbox")["items"][0]["authorization_mode"] == "human"


def test_explicit_human_inbound_and_automated_receipt_have_distinct_actions(review_api):
    state = review_api
    ingest_records(state.connection, opportunities=[
        _record("human"), _record("receipt"),
    ], activities=[
        _reply("human", type="email_received", classification="human"),
        _reply("receipt", type="application_received", classification="automated"),
    ])
    records = {item["id"]: item for item in _get(state, "/records")["items"]}
    assert records["human"]["next_action"] == "reply"
    assert records["receipt"]["next_action"] != "reply"
    assert records["receipt"]["activities"][0]["classification"] == "automated"


def test_inbox_retains_unanswered_replies_during_cooldown_and_after_unsent_approval(review_api):
    state = review_api
    draft = _draft(state)
    _post(state, f"/drafts/{draft['id']}/approve", {"review_token": draft["review_token"]})
    ingest_records(state.connection, opportunities=[
        _record("cooldown", last_contact_at=NOW - timedelta(hours=2)),
        _record("receipt"),
    ], activities=[
        _reply("cooldown", type="email_received", classification="human"),
        _reply("receipt", type="contact_replied", classification="automated"),
    ])
    inbox = _get(state, "/inbox")
    assert inbox["total"] == 2
    assert inbox["source_freshness"] == "unknown"
    assert inbox["outreach_eligibility"] == "not_evaluated"
    by_contact = {item["contact_key"]: item for item in inbox["items"]}
    assert set(by_contact) == {"person:one", "person:cooldown"}
    assert by_contact["person:one"]["pending_outbox_count"] == 1
    assert by_contact["person:cooldown"]["last_outbound_at"] is not None
    assert len(state.calls) == 1
    assert _get(state, "/inbox?limit=1")["has_more"]
    ingest_records(state.connection, activities=[_reply(
        id="one:sent", type="message_sent", direction="outbound",
        occurred_at=NOW - timedelta(minutes=1),
    )])
    assert _get(state, "/inbox")["total"] == 1
    assert len(state.calls) == 1


def test_inbox_and_outbox_refs_remain_complete_beyond_the_first_record_page(review_api):
    state = review_api
    draft = _draft(state, "off-page")
    _post(state, f"/drafts/{draft['id']}/approve", {"review_token": draft["review_token"]})
    keys = [f"queued-{index:02}" for index in range(55)]
    ingest_records(state.connection, opportunities=[
        _record("off-page", kind="job_application", title="Older application"),
        *[_record(key) for key in keys],
    ], activities=[_reply(key) for key in keys])
    _post(state, "/sync")
    page = _get(state, "/records")
    assert page["total"] == 56 and page["has_more"]
    assert "off-page" not in {record["id"] for record in page["items"]}
    expected = [{"id": "off-page", "kind": "job_application", "title": "Older application"}]
    assert _get(state, "/outbox")["items"][0]["record_refs"] == expected
    inbox_item = next(item for item in _get(state, "/inbox")["items"]
                      if item["contact_key"] == "person:off-page")
    assert inbox_item["record_refs"] == expected
    assert inbox_item["review_record_id"] == "off-page"
    assert _get(state, "/records/off-page")["draft"]["status"] == "approved"
    assert len(state.calls) == 1


def test_grouped_inbox_navigates_to_the_contact_candidate_primary(review_api):
    state = review_api
    ingest_records(state.connection, opportunities=[
        _record(key, contact_key="same-contact", contact_email="shared@example.com",
                value=value, created_at=NOW - timedelta(days=10))
        for key, value in (("primary-viewed", 1000), ("reply-evidence", 100))
    ], activities=[
        {"id": "new-view", "opportunity_id": "primary-viewed", "type": "content_viewed",
         "occurred_at": NOW - timedelta(days=2)},
        _reply("reply-evidence", occurred_at=NOW - timedelta(days=6)),
    ])
    _post(state, "/sync")
    inbox = _get(state, "/inbox")["items"]
    assert len(inbox) == 1
    assert inbox[0]["opportunity_ids"] == ["reply-evidence"]
    assert inbox[0]["review_record_id"] == "primary-viewed"
    assert inbox[0]["record_refs"] == [{"id": "reply-evidence", "kind": "generic", "title": "reply-evidence"}]
    primary = _get(state, "/records/primary-viewed")
    assert primary["candidate_id"] is not None
    assert primary["referenced_record_ids"] == ["reply-evidence"]
    assert _get(state, "/records/reply-evidence")["candidate_id"] is None
    legacy = state.client.get("/v1/inbox").json()["items"][0]
    assert "record_refs" not in legacy and "review_record_id" not in legacy
    assert state.calls == []


def test_clean_missing_validation_and_provider_failure_responses(review_api):
    state = review_api
    _post(state, "/records/missing/draft", status=404)
    assert state.client.get("/v1/ui/records/missing", headers=HEADERS).status_code == 404
    _post(state, f"/drafts/{uuid4()}/approve", {"review_token": "0" * 64}, status=404)
    _post(state, "/sync", {"limit": 201}, status=422)
    _post(state, "/sync", {"review": {"mode": "automatic"}}, status=422)
    assert state.calls == []
    ingest_records(state.connection, opportunities=[_record()], activities=[_reply()])
    _post(state, "/sync")

    def failure(**kwargs):
        raise RuntimeError("secret upstream details must not reach the browser")

    state.adapter = replace(state.adapter, completion_fn=failure)
    result = _post(state, "/records/one/draft", status=503)
    assert "secret" not in str(result)
    assert state.connection.execute(text("SELECT count(*) FROM drafts")).scalar_one() == 0


@pytest.mark.parametrize("commit_fails", [False, True])
def test_approval_commits_before_http_success(dependencies, monkeypatch, commit_fails):
    events = []
    draft_id = uuid4()

    @contextmanager
    def transaction():
        yield object()
        events.append("commit")
        if commit_fails:
            raise SQLAlchemyError("sensitive driver diagnostic")

    app.dependency_overrides[get_api_engine] = lambda: SimpleNamespace(begin=transaction)
    app.dependency_overrides[get_workflow_policy] = lambda: load_policy(DEFAULT_POLICY_PATH)
    app.dependency_overrides[get_workflow_clock] = lambda: lambda: NOW
    monkeypatch.setattr(ui, "_require_draft", lambda *_args: object())
    monkeypatch.setattr(ui, "approve_draft", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(ui, "_draft_response", lambda *_args: UIDraft(
        id=draft_id, body="Reviewed copy", status="approved", review_token="0" * 64, outbox_id=1,
    ))

    async def request():
        path = f"/v1/ui/drafts/{draft_id}/approve"
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"", "headers": [(b"content-type", b"application/json"),
                (b"authorization", f"Bearer {TOKEN}".encode())],
            "client": ("test", 123), "server": ("test", 80),
        }

        async def receive():
            return {"type": "http.request", "body": json.dumps({"review_token": "0" * 64}).encode(), "more_body": False}

        async def send(message):
            events.append(message)

        await app(scope, receive, send)

    asyncio.run(request())
    assert events[0] == "commit"
    starts = [event for event in events[1:] if event["type"] == "http.response.start"]
    assert len(starts) == 1 and starts[0]["status"] == (503 if commit_fails else 200)
    if commit_fails:
        bodies = b"".join(event["body"] for event in events[1:] if event["type"] == "http.response.body")
        assert json.loads(bodies) == {"detail": "Database unavailable"}
