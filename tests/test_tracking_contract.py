from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from respawned.core.candidates import select_candidates
from respawned.core.context import BusinessContext
from respawned.core.contracts import ActivityIn, OpportunityIn
from respawned.core.domain import OpportunityContext, OpportunityState
from respawned.core.ingest import IngestConflictError, ingest_records
from respawned.core.policy import Policy, RankingPolicy, ReasonPolicy
from respawned.core.reasons import EVALUATORS, ReasonContext, ReasonMatch
from respawned.core.reduce import reduce_opportunities
from respawned.core.score import score_opportunities


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def _application(**changes):
    return {
        "id": "application-1",
        "status": "open",
        "created_at": NOW - timedelta(days=12),
        "kind": "job_application",
        "title": "Software engineer at Example",
        "context": {"company": "Example", "role": "Software engineer", "stage": "Applied"},
    } | changes


def _state(**changes):
    return OpportunityState(
        opportunity_id="application-1", status="open", kind="job_application",
        created_at=NOW - timedelta(days=12), **changes,
    )


def _context(state, outbound=None):
    return ReasonContext(state, NOW, ZoneInfo("UTC"), outbound)


def _policy():
    return Policy(
        business_context=BusinessContext(), cooldown_hours=Decimal("72"),
        dead_after_days=45, ranking=RankingPolicy(Decimal("20"), Decimal("20")),
        reasons={
            "application_no_update": ReasonPolicy(
                "application_no_update", Decimal("42"), "professional",
                {"minimum_days": 7, "horizon_days": 14},
            ),
            "high_value_quiet": ReasonPolicy(
                "high_value_quiet", Decimal("60"), "professional",
                {"high_percentile": Decimal(".75"), "quiet_days": 3, "horizon_days": 14},
            ),
        },
    )


def test_contactless_record_has_no_fabricated_identity_or_route():
    record = OpportunityIn.model_validate(_application())
    assert record.contact_key is None
    assert record.contact_email is None
    assert record.contact_phone is None
    assert record.context.company == "Example"


@pytest.mark.parametrize("changes", [
    {"contact_key": "recruiter-1"},
    {"contact_email": "recruiter@example.com"},
    {"preferred_channel": "email"},
])
def test_partial_contact_identity_is_rejected(changes):
    with pytest.raises(ValidationError):
        OpportunityIn.model_validate(_application(**changes))


@pytest.mark.parametrize("changes", [
    {"kind": ""}, {"kind": "Job Application"}, {"kind": "x" * 65},
    {"title": "x" * 301}, {"context": {"company": "x" * 201}},
    {"context": {"summary": "x" * 2001}},
    {"context": {"expected_reply_at": "2026-09-09T12:00:00"}},
    {"context": {"source_url": "javascript:alert(1)"}},
    {"context": {"source_url": "https://user:secret@example.com"}},
    {"context": {"policy": {"review": "automatic"}}},
])
def test_context_and_kind_are_bounded_data(changes):
    with pytest.raises(ValidationError):
        OpportunityIn.model_validate(_application(**changes))


def test_legacy_contact_records_and_activities_keep_defaults():
    record = OpportunityIn.model_validate({
        "id": "legacy", "contact_key": "known-1", "contact_email": "person@example.com",
        "created_at": NOW, "status": "open",
    })
    activity = ActivityIn.model_validate({
        "id": "legacy-reply", "type": "contact_replied", "opportunity_id": "legacy",
        "occurred_at": NOW,
    })
    assert record.kind == "generic"
    assert record.context.model_dump(exclude_none=True) == {}
    assert activity.classification == "unknown"


def test_jobs_rank_without_money_but_contactless_records_never_become_candidates():
    policy = _policy()
    without_value = _state()
    with_value = replace(without_value, value=Decimal("999999.00"))
    first = score_opportunities([without_value], policy, NOW)
    second = score_opportunities([with_value], policy, NOW)
    assert first == second
    assert first[0].value_percentile == 0
    assert [reason.reason for reason in first[0].reasons] == ["application_no_update"]
    assert select_candidates([without_value], first, NOW, policy) == []


def test_application_wait_uses_human_updates_and_contact_cooldown():
    evaluator = EVALUATORS["application_no_update"]({"minimum_days": 7, "horizon_days": 14})
    state = _state()
    assert evaluator(_context(state)) == ReasonMatch(Decimal(5) / 14, state.created_at)
    assert evaluator(_context(replace(state, kind="generic"))) is None
    assert evaluator(_context(replace(state, last_replied_at=NOW - timedelta(days=1)))) is None
    contacted = replace(state, contact_key="recruiter", contact_email="r@example.com", last_outbound_at=NOW - timedelta(hours=1))
    assert score_opportunities([contacted], _policy(), NOW) == []


def test_awaiting_reply_observes_reply_and_minimum_wait_boundaries():
    evaluator = EVALUATORS["awaiting_reply"]({"minimum_days": 7, "horizon_days": 14})
    outbound = NOW - timedelta(days=9)
    assert evaluator(_context(_state(), outbound)) == ReasonMatch(Decimal(2) / 14, outbound)
    assert evaluator(_context(_state(), NOW - timedelta(days=6))) is None
    assert evaluator(_context(_state(last_replied_at=outbound), outbound)) is None
    assert evaluator(_context(_state(last_replied_at=NOW + timedelta(days=1)), outbound)) is not None


def test_promised_deadline_requires_overdue_date_and_stops_after_a_new_exchange():
    evaluator = EVALUATORS["promised_update_overdue"]({"grace_days": 1, "horizon_days": 7})
    expected = NOW - timedelta(days=3)
    state = _state(context=OpportunityContext(expected_reply_at=expected))
    assert evaluator(_context(state)) == ReasonMatch(Decimal(2) / 7, expected)
    assert evaluator(_context(replace(state, context=OpportunityContext(expected_reply_at=NOW)))) is None
    assert evaluator(_context(replace(state, last_replied_at=expected))) is None
    assert evaluator(_context(state, expected)) is None


def test_contactless_metadata_and_classification_round_trip(postgres_connection):
    application = _application(context={
        "company": "Example", "role": "Software engineer", "stage": "Interview",
        "summary": "Discussed the infrastructure team.",
        "expected_reply_at": (NOW - timedelta(days=2)).isoformat(),
        "source_url": "https://example.com/applications/1",
    })
    automated = {
        "id": "receipt", "opportunity_id": "application-1", "type": "contact_replied",
        "occurred_at": NOW - timedelta(days=1), "classification": "automated",
        "summary": "Application received", "source_url": "https://example.com/receipt",
        "direction": "inbound",
    }
    first = ingest_records(postgres_connection, opportunities=[application], activities=[automated])
    replay = ingest_records(postgres_connection, opportunities=[application], activities=[automated])
    [state] = reduce_opportunities(postgres_connection, NOW)
    assert first.activities_inserted == 1
    assert replay.activities_inserted == 0
    assert state.contact_key is None
    assert state.context.expected_reply_at == NOW - timedelta(days=2)
    assert state.activities[0].classification == "automated"
    assert state.activities[0].summary == "Application received"
    assert state.last_replied_at is None
    assert score_opportunities([state], _policy(), NOW)

    with pytest.raises(IngestConflictError, match="different payload"):
        ingest_records(postgres_connection, activities=[automated | {"classification": "human"}])


def test_human_inbound_and_legacy_replies_reduce_but_automated_replies_do_not(postgres_connection):
    application = _application()
    activities = [
        {"id": "legacy", "type": "contact_replied", "occurred_at": NOW - timedelta(days=5)},
        {"id": "human", "type": "interview_update", "direction": "inbound", "classification": "human", "occurred_at": NOW - timedelta(days=3)},
        {"id": "auto", "type": "contact_replied", "classification": "automated", "occurred_at": NOW - timedelta(days=1)},
    ]
    ingest_records(postgres_connection, opportunities=[application], activities=[
        activity | {"opportunity_id": "application-1"} for activity in activities
    ])
    [legacy_state] = reduce_opportunities(postgres_connection, NOW - timedelta(days=4))
    [current_state] = reduce_opportunities(postgres_connection, NOW)
    assert legacy_state.last_replied_at == NOW - timedelta(days=5)
    assert current_state.last_replied_at == NOW - timedelta(days=3)
