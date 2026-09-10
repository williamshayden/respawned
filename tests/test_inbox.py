"""Reply visibility must not weaken outbound approval or cooldown policy."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from respawned.cli import inbox as inbox_cli
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.ingest import ingest_records
from respawned.core.inbox import list_reply_inbox
from respawned.core.policy import load_policy
from respawned.core.review import approve_draft, draft_candidate
from respawned.core.sync import sync_candidates
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
POLICY = load_policy(DEFAULT_POLICY_PATH)


def _opportunity(id="one", **values):
    return {
        "id": id, "contact_key": "crm:avery", "contact_name": "Avery",
        "contact_email": "avery@example.com", "preferred_channel": "email",
        "status": "open", "created_at": NOW - timedelta(days=3),
        **values,
    }


def _activity(id="reply", opportunity_id="one", **values):
    return {
        "id": id, "opportunity_id": opportunity_id, "type": "contact_replied",
        "occurred_at": NOW - timedelta(hours=1), "direction": "inbound",
        "channel": "email", **values,
    }


def _inbox(connection, **values):
    return list_reply_inbox(connection, now=NOW, policy=POLICY, **values)


def test_fresh_reply_visible_during_cooldown_without_writes(postgres_connection):
    connection = postgres_connection
    outbound = NOW - timedelta(hours=2)
    ingest_records(connection, opportunities=[_opportunity(last_contact_at=outbound)],
                   activities=[_activity()])
    assert sync_candidates(connection, now=NOW, policy=POLICY, dry_run=True).candidates == ()

    result = _inbox(connection)

    assert result.total == 1
    assert result.items[0].last_outbound_at == outbound
    assert result.items[0].reply_evidence[0].activity_id == "reply"
    assert result.items[0].reply_evidence[0].occurred_at == NOW - timedelta(hours=1)
    assert result.source_freshness == "unknown"
    assert result.outreach_eligibility == "not_evaluated"
    for table in ("sync_runs", "candidates", "drafts", "outbox"):
        assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_later_contact_wide_outbound_resolves_replies(postgres_connection):
    connection = postgres_connection
    ingest_records(connection, opportunities=[_opportunity(), _opportunity("closed", status="won")],
                   activities=[_activity(), _activity("future", "closed", type="message_sent",
                       direction="outbound", occurred_at=NOW + timedelta(minutes=1))])
    assert _inbox(connection).total == 1
    ingest_records(connection, activities=[_activity("answer", "closed", type="message_sent",
                   direction="outbound", occurred_at=NOW - timedelta(minutes=1))])
    assert _inbox(connection).total == 0


def test_equal_timestamps_do_not_prove_a_reply_was_answered(postgres_connection):
    ingest_records(postgres_connection,
                   opportunities=[_opportunity(last_contact_at=NOW - timedelta(hours=1))],
                   activities=[_activity()])
    assert _inbox(postgres_connection).total == 1


@pytest.mark.parametrize("overrides,activity_values", [
    ({"status": "lost"}, {}),
    ({"created_at": NOW - timedelta(days=POLICY.dead_after_days)}, {}),
    ({}, {"type": "automated_reply"}),
    ({}, {"type": "application_confirmation"}),
    ({}, {"classification": "automated"}),
    ({}, {"direction": "outbound"}),
    ({}, {"direction": None}),
    ({}, {"occurred_at": NOW + timedelta(seconds=1)}),
])
def test_nonactionable_or_unconfirmed_human_replies_excluded(
    postgres_connection, overrides, activity_values
):
    ingest_records(postgres_connection, opportunities=[_opportunity(**overrides)],
                   activities=[_activity(**activity_values)])
    assert _inbox(postgres_connection).items == ()


def test_explicit_human_inbound_with_source_type_is_visible(postgres_connection):
    ingest_records(postgres_connection, opportunities=[_opportunity()], activities=[
        _activity(type="email_received", classification="human"),
        _activity(id="newer-receipt", classification="automated", occurred_at=NOW),
    ])
    result = _inbox(postgres_connection)
    assert result.total == 1
    assert result.items[0].reply_evidence[0].activity_id == "reply"
    assert result.items[0].latest_reply_at == NOW - timedelta(hours=1)


def test_contactless_human_response_stays_out_of_route_based_inbox(postgres_connection):
    ingest_records(postgres_connection, opportunities=[_opportunity(
        contact_key=None, contact_email=None, preferred_channel=None,
    )], activities=[_activity(classification="human")])
    assert _inbox(postgres_connection).items == ()


def test_grouping_preserves_source_evidence_and_distinct_routes(postgres_connection):
    ingest_records(postgres_connection, opportunities=[
        _opportunity(), _opportunity("two", contact_email="AVERY@example.com"),
        _opportunity("other-route", contact_email="other@example.com"),
        _opportunity("other-identity", contact_key="crm:other-person"),
    ], activities=[
        _activity("one-reply"), _activity("two-reply", "two"),
        _activity("other-route-reply", "other-route"),
        _activity("other-person-reply", "other-identity"),
    ])

    result = _inbox(postgres_connection)

    assert result.total == 3
    grouped = next(item for item in result.items if item.opportunity_ids == ("one", "two"))
    assert [(e.opportunity_id, e.activity_id) for e in grouped.reply_evidence] == [
        ("one", "one-reply"), ("two", "two-reply"),
    ]
    limited = _inbox(postgres_connection, limit=1)
    assert limited.items == result.items[:1]
    assert limited.total == 3
    assert limited.has_more


def test_pending_outbox_reservation_does_not_answer_reply(postgres_connection):
    connection = postgres_connection
    ingest_records(connection, opportunities=[_opportunity()], activities=[_activity()])
    candidate = sync_candidates(connection, now=NOW, policy=POLICY).candidates[0]
    adapter = LiteLLMAdapter(
        "http://unused.test", "unused", "scripted",
        completion_fn=lambda **_kwargs: {"choices": [{"message": {
            "content": "Hi Avery, thanks for your reply. What would be helpful?"
        }}]},
    )
    draft = draft_candidate(connection, candidate=candidate, now=NOW, policy=POLICY, adapter=adapter)
    assert draft is not None
    approve_draft(connection, draft_id=draft.id, now=NOW, policy=POLICY,
                  expected_review_token=draft.review_token)

    result = _inbox(connection)

    assert result.total == 1
    assert result.items[0].pending_outbox_count == 1
    assert sync_candidates(connection, now=NOW, policy=POLICY, dry_run=True).candidates == ()


def test_cli_json_preserves_the_api_evidence_without_interpreting_source_markup(
    postgres_connection, monkeypatch, capsys
):
    ingest_records(postgres_connection, opportunities=[_opportunity(contact_name="[red]Avery")],
                   activities=[_activity()])
    payload = json.loads(json.dumps(asdict(_inbox(postgres_connection)), default=lambda item: item.isoformat()))
    calls = []
    client = SimpleNamespace(inbox=lambda **kwargs: calls.append(kwargs) or payload)
    monkeypatch.setattr(inbox_cli, "client_from_args", lambda _args: client)
    assert inbox_cli.main(["--json", "--limit", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == payload and calls == [{"limit": 1}]
    assert result["source_freshness"] == "unknown"
    assert result["items"][0]["reply_evidence"][0]["activity_id"] == "reply"
    assert inbox_cli.main([]) == 0
    assert "[red]Avery" in capsys.readouterr().out


@pytest.mark.parametrize("limit", [0, -1, 201, True, 1.5])
def test_limit_validation_precedes_database_access(limit):
    with pytest.raises(ValueError, match="between 1 and 200"):
        _inbox(None, limit=limit)


def test_cli_rejects_local_cutoff_before_creating_http_client(monkeypatch, capsys):
    from respawned.__main__ import main

    monkeypatch.setattr(inbox_cli, "client_from_args", lambda _args: pytest.fail("created HTTP client"))
    with pytest.raises(SystemExit) as exc:
        main(["inbox", "--now", "2026-09-08T12:00:00"])
    assert exc.value.code == 2
    assert "The engine owns time and policy" in capsys.readouterr().err
