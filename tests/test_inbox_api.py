"""HTTP contract for the read-only reply inbox; core behavior has database tests."""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from respawned.api import app as api_module
from respawned.core.inbox import ReplyEvidence, ReplyInboxItem, ReplyInboxResult


@pytest.fixture
def inbox_client(monkeypatch):
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "inbox-test-operator")
    calls = []

    @contextmanager
    def transaction():
        yield object()

    def list_inbox(connection, *, now, policy, limit):
        calls.append((now, policy, limit))
        evidence = ReplyEvidence("opp-1", "reply-1", now, "email")
        item = ReplyInboxItem(
            contact_key="contact-1",
            contact_name="Ada",
            channel="email",
            contact_address="ada@example.com",
            opportunity_ids=("opp-1",),
            latest_reply_at=now,
            last_outbound_at=None,
            reply_evidence=(evidence,),
            pending_outbox_count=1,
        )
        return ReplyInboxResult(now, (item,), 2, True)

    monkeypatch.setitem(
        api_module.app.dependency_overrides, api_module.get_api_engine,
        lambda: SimpleNamespace(begin=transaction)
    )
    monkeypatch.setattr(api_module, "list_reply_inbox", list_inbox)
    with TestClient(api_module.app, headers={"Authorization": "Bearer inbox-test-operator"}) as client:
        yield client, calls


def test_inbox_exposes_evidence_and_limits_without_claiming_eligibility(inbox_client):
    client, calls = inbox_client
    response = client.get(
        "/v1/inbox", params={"now": "2026-09-08T08:00:00-04:00", "limit": 1}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["as_of"] == "2026-09-08T12:00:00Z"
    assert result["total"] == 2
    assert result["has_more"] is True
    assert result["source_freshness"] == "unknown"
    assert result["outreach_eligibility"] == "not_evaluated"
    assert result["items"][0]["pending_outbox_count"] == 1
    assert result["items"][0]["reply_evidence"] == [
        {
            "opportunity_id": "opp-1",
            "activity_id": "reply-1",
            "occurred_at": "2026-09-08T12:00:00Z",
            "channel": "email",
        }
    ]
    assert calls[0][0] == datetime(2026, 9, 8, 12, tzinfo=UTC)
    assert calls[0][2] == 1


@pytest.mark.parametrize(
    "params",
    [
        {"now": "2026-09-08T12:00:00"},
        {"now": "invalid"},
        {"limit": 0},
        {"limit": -1},
        {"limit": 201},
    ],
)
def test_inbox_rejects_invalid_queries(inbox_client, params):
    client, calls = inbox_client
    assert client.get("/v1/inbox", params=params).status_code == 422
    assert calls == []


def test_inbox_defaults_to_current_utc_and_bounded_result(inbox_client):
    client, calls = inbox_client
    before = datetime.now(UTC)
    assert client.get("/v1/inbox").status_code == 200
    assert before <= calls[0][0] <= datetime.now(UTC)
    assert calls[0][2] == 50
