from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from respawned.core.candidates import Candidate
from respawned.core.policy import load_policy
from respawned.core.review import (
    ReviewBlockedError,
    approve_draft,
    draft_candidate,
    enqueue_outbox,
    iter_candidate_drafts,
    load_latest_candidates,
    reject_draft,
    update_draft_message,
)
from respawned.llm.adapter import LiteLLMAdapter

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
POLICY_PATH = (
    Path(__file__).parents[1] / "src" / "respawned" / "config" / "policy.yaml"
)


def _adapter(calls=None) -> LiteLLMAdapter:
    calls = calls if calls is not None else []

    def completion(**kwargs):
        calls.append(kwargs["messages"])
        return {"choices": [{"message": {"content": "Hi Jamie, just checking in."}}]}

    return LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=completion,
    )


def _failing_adapter() -> LiteLLMAdapter:
    def completion(**_kwargs):
        raise RuntimeError("proxy unavailable")

    return LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=completion,
    )


def _insert_opportunity(
    connection,
    *,
    opportunity_id: str,
    contact_key: str,
    contact_address: str,
    channel: str = "sms",
    contact_name: str | None = "Jamie",
    owner_name: str | None = "Sam",
    created_at: datetime | None = None,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO opportunities (
                id, contact_key, contact_name, contact_phone, contact_email,
                owner_name, value, status, created_at, preferred_channel
            ) VALUES (
                :id, :contact_key, :contact_name, :contact_phone, :contact_email,
                :owner_name, 1250, 'open', :created_at, :channel
            )
            """
        ),
        {
            "id": opportunity_id,
            "contact_key": contact_key,
            "contact_name": contact_name,
            "contact_phone": contact_address if channel == "sms" else None,
            "contact_email": contact_address if channel == "email" else None,
            "owner_name": owner_name,
            "created_at": created_at or NOW - timedelta(days=2),
            "channel": channel,
        },
    )


def _insert_candidate(
    connection,
    *,
    opportunity_id: str,
    contact_key: str,
    contact_address: str,
    channel: str = "sms",
    contact_name: str = "Jamie",
    score: str = "90",
    other_opportunity_ids: tuple[str, ...] = (),
    run_id=None,
) -> Candidate:
    candidate = Candidate(
        id=uuid4(),
        run_at=NOW,
        primary_opportunity_id=opportunity_id,
        contact_key=contact_key,
        contact_address=contact_address,
        contact_name=contact_name,
        channel=channel,
        reason="replied_no_answer",
        score=Decimal(score),
        other_opportunity_ids=other_opportunity_ids,
    )
    active_run_id = run_id or uuid4()
    if run_id is None:
        connection.execute(
            text(
                "INSERT INTO sync_runs (id, run_at, candidate_count) "
                "VALUES (:id, :run_at, 1)"
            ),
            {"id": active_run_id, "run_at": NOW},
        )
    connection.execute(
        text(
            """
            INSERT INTO candidates (
                id, sync_run_id, run_at, primary_opportunity_id,
                contact_key, contact_address, contact_name, channel,
                reason, score, other_opportunity_ids
            ) VALUES (
                :id, :sync_run_id, :run_at, :primary_opportunity_id,
                :contact_key, :contact_address, :contact_name, :channel,
                :reason, :score, :other_opportunity_ids
            )
            """
        ),
        {
            "id": candidate.id,
            "sync_run_id": active_run_id,
            "run_at": candidate.run_at,
            "primary_opportunity_id": candidate.primary_opportunity_id,
            "contact_key": candidate.contact_key,
            "contact_address": candidate.contact_address,
            "contact_name": candidate.contact_name,
            "channel": candidate.channel,
            "reason": candidate.reason,
            "score": candidate.score,
            "other_opportunity_ids": list(candidate.other_opportunity_ids),
        },
    )
    return candidate


def _draft(connection, candidate: Candidate):
    return draft_candidate(
        connection,
        candidate=candidate,
        now=NOW,
        policy=load_policy(POLICY_PATH),
        adapter=_adapter(),
    )


def test_model_failure_blocks_without_persisting_a_draft(postgres_connection):
    key = "crm:model-failure"
    opportunity_id = "REVIEW-MODEL-FAILURE"
    _insert_opportunity(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address="model@example.com",
        channel="email",
    )
    candidate = _insert_candidate(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address="model@example.com",
        channel="email",
    )

    with pytest.raises(ReviewBlockedError, match="draft generation failed"):
        draft_candidate(
            postgres_connection,
            candidate=candidate,
            now=NOW,
            policy=load_policy(POLICY_PATH),
            adapter=_failing_adapter(),
        )

    assert (
        postgres_connection.execute(text("SELECT COUNT(*) FROM drafts")).scalar_one()
        == 0
    )


@pytest.mark.parametrize(
    ("channel", "address"),
    (("sms", "+1 (312) 555-0101"), ("email", "jamie@example.com")),
)
def test_approval_is_idempotent_and_preserves_actual_route(
    postgres_connection, channel, address
):
    key = f"crm:{channel}:1"
    opportunity_id = f"REVIEW-{channel.upper()}"
    _insert_opportunity(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address=address,
        channel=channel,
    )
    candidate = _insert_candidate(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address=address,
        channel=channel,
    )
    draft = _draft(postgres_connection, candidate)
    policy = load_policy(POLICY_PATH)

    first = approve_draft(
        postgres_connection,
        draft_id=draft.id,
        expected_review_token=draft.review_token,
        now=NOW,
        policy=policy,
    )
    second = approve_draft(
        postgres_connection,
        draft_id=draft.id,
        expected_review_token=draft.review_token,
        now=NOW,
        policy=policy,
    )
    row = postgres_connection.execute(
        text(
            """
            SELECT id, draft_id, contact_key, contact_address, channel,
                   opportunity_ids, body
            FROM outbox WHERE draft_id = :draft_id
            """
        ),
        {"draft_id": draft.id},
    ).one()

    assert first == second == row.id
    assert row.contact_key == key
    assert row.contact_address == address
    assert row.channel == channel
    assert row.opportunity_ids == [opportunity_id]
    assert row.body == draft.body


@pytest.mark.parametrize("contact_source", ("opportunity_state", "outbox"))
def test_approval_blocks_cooldown_consumed_after_drafting(
    postgres_connection, contact_source
):
    key = f"crm:cooldown:{contact_source}"
    address = "+13125550102"
    opportunity_id = f"REVIEW-COOLDOWN-{contact_source}"
    _insert_opportunity(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address=address,
    )
    candidate = _insert_candidate(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address=address,
    )
    draft = _draft(postgres_connection, candidate)

    if contact_source == "opportunity_state":
        postgres_connection.execute(
            text("UPDATE opportunities SET last_contact_at = :at WHERE id = :id"),
            {"at": NOW - timedelta(hours=1), "id": opportunity_id},
        )
    else:
        reservation_candidate = _insert_candidate(
            postgres_connection,
            opportunity_id=opportunity_id,
            contact_key=key,
            contact_address=address,
        )
        reservation = _draft(postgres_connection, reservation_candidate)
        enqueue_outbox(
            postgres_connection,
            draft_id=reservation.id,
            contact_key=key,
            contact_address=address,
            contact_name="Jamie",
            channel="sms",
            opportunity_ids=reservation.opportunity_ids,
            body=reservation.body,
            created_at=NOW - timedelta(hours=1),
        )

    with pytest.raises(ReviewBlockedError, match="cooldown"):
        approve_draft(
            postgres_connection,
            draft_id=draft.id,
            expected_review_token=draft.review_token,
            now=NOW,
            policy=load_policy(POLICY_PATH),
        )
    assert (
        postgres_connection.execute(
            text("SELECT COUNT(*) FROM outbox WHERE draft_id = :draft_id"),
            {"draft_id": draft.id},
        ).scalar_one()
        == 0
    )


@pytest.mark.parametrize("share_contact_key", (True, False))
def test_outbox_cooldown_uses_identity_not_route(
    postgres_connection, share_contact_key
):
    first_key = "crm:identity:first"
    second_key = first_key if share_contact_key else "crm:identity:second"
    first_route = (
        ("+13125550120", "sms")
        if share_contact_key
        else ("shared@example.com", "email")
    )
    drafts = []
    for suffix, key, address, channel in (
        ("first", first_key, *first_route),
        ("second", second_key, "shared@example.com", "email"),
    ):
        opportunity_id = f"REVIEW-IDENTITY-{suffix}"
        _insert_opportunity(
            postgres_connection,
            opportunity_id=opportunity_id,
            contact_key=key,
            contact_address=address,
            channel=channel,
        )
        drafts.append(
            _draft(
                postgres_connection,
                _insert_candidate(
                    postgres_connection,
                    opportunity_id=opportunity_id,
                    contact_key=key,
                    contact_address=address,
                    channel=channel,
                ),
            )
        )

    enqueue_outbox(
        postgres_connection,
        draft_id=drafts[1].id,
        contact_key=second_key,
        contact_address="shared@example.com",
        contact_name="Jamie",
        channel="email",
        opportunity_ids=drafts[1].opportunity_ids,
        body=drafts[1].body,
        created_at=NOW - timedelta(hours=1),
    )

    def approve_first():
        return approve_draft(
            postgres_connection,
            draft_id=drafts[0].id,
            expected_review_token=drafts[0].review_token,
            now=NOW,
            policy=load_policy(POLICY_PATH),
        )

    if share_contact_key:
        with pytest.raises(ReviewBlockedError, match="cooldown"):
            approve_first()
    else:
        assert isinstance(approve_first(), int)


def test_approval_blocks_when_a_mentioned_opportunity_closes(postgres_connection):
    key = "crm:mentioned"
    address = "+13125550103"
    primary_id = "REVIEW-PRIMARY-OPEN"
    mentioned_id = "REVIEW-MENTIONED-CLOSED"
    for opportunity_id in (primary_id, mentioned_id):
        _insert_opportunity(
            postgres_connection,
            opportunity_id=opportunity_id,
            contact_key=key,
            contact_address=address,
        )
    candidate = _insert_candidate(
        postgres_connection,
        opportunity_id=primary_id,
        contact_key=key,
        contact_address=address,
        other_opportunity_ids=(mentioned_id,),
    )
    draft = _draft(postgres_connection, candidate)
    postgres_connection.execute(
        text("UPDATE opportunities SET status = 'lost' WHERE id = :id"),
        {"id": mentioned_id},
    )

    with pytest.raises(ReviewBlockedError, match="no longer open/contactable"):
        approve_draft(
            postgres_connection,
            draft_id=draft.id,
            expected_review_token=draft.review_token,
            now=NOW,
            policy=load_policy(POLICY_PATH),
        )


def test_approval_blocks_when_draft_ages_past_dead_threshold(postgres_connection):
    key = "crm:aged"
    address = "+13125550109"
    opportunity_id = "REVIEW-AGED-OUT"
    _insert_opportunity(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address=address,
        created_at=NOW - timedelta(days=44),
    )
    draft = _draft(
        postgres_connection,
        _insert_candidate(
            postgres_connection,
            opportunity_id=opportunity_id,
            contact_key=key,
            contact_address=address,
        ),
    )

    with pytest.raises(ReviewBlockedError, match="no longer open/contactable"):
        approve_draft(
            postgres_connection,
            draft_id=draft.id,
            expected_review_token=draft.review_token,
            now=NOW + timedelta(days=1),
            policy=load_policy(POLICY_PATH),
        )


def test_route_change_blocks_approval_and_preserves_empty_outbox(postgres_connection):
    key = "crm:route-change"
    opportunity_id = "REVIEW-ROUTE-CHANGE"
    _insert_opportunity(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address="+13125550111",
    )
    draft = _draft(
        postgres_connection,
        _insert_candidate(
            postgres_connection,
            opportunity_id=opportunity_id,
            contact_key=key,
            contact_address="+13125550111",
        ),
    )
    postgres_connection.execute(
        text("UPDATE opportunities SET contact_phone = '+13125550999' WHERE id = :id"),
        {"id": opportunity_id},
    )

    with pytest.raises(ReviewBlockedError, match="route changed"):
        approve_draft(
            postgres_connection,
            draft_id=draft.id,
            expected_review_token=draft.review_token,
            now=NOW,
            policy=load_policy(POLICY_PATH),
        )
    assert (
        postgres_connection.execute(text("SELECT COUNT(*) FROM outbox")).scalar_one()
        == 0
    )


def test_drafting_blocks_a_stale_route_before_calling_the_model(postgres_connection):
    key = "crm:stale-draft"
    opportunity_id = "REVIEW-STALE-DRAFT"
    _insert_opportunity(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address="+13125550112",
    )
    candidate = _insert_candidate(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address="+13125550112",
    )
    postgres_connection.execute(
        text("UPDATE opportunities SET contact_phone = '+13125550888' WHERE id = :id"),
        {"id": opportunity_id},
    )
    calls = []

    with pytest.raises(ReviewBlockedError, match="route changed"):
        draft_candidate(
            postgres_connection,
            candidate=candidate,
            now=NOW,
            policy=load_policy(POLICY_PATH),
            adapter=_adapter(calls),
        )
    assert calls == []


def test_edit_then_reject_persists_without_delivery(postgres_connection):
    key = "crm:reject"
    address = "+13125550113"
    opportunity_id = "REVIEW-REJECT"
    _insert_opportunity(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address=address,
    )
    draft = _draft(
        postgres_connection,
        _insert_candidate(
            postgres_connection,
            opportunity_id=opportunity_id,
            contact_key=key,
            contact_address=address,
        ),
    )
    with pytest.raises(ReviewBlockedError, match="failed validation"):
        update_draft_message(
            postgres_connection,
            draft_id=draft.id,
            expected_review_token=draft.review_token,
            body="Call me about $100",
            now=NOW,
            policy=load_policy(POLICY_PATH),
        )
    edited = update_draft_message(
        postgres_connection,
        draft_id=draft.id,
        expected_review_token=draft.review_token,
        body="Hi Jamie, following up.\n\nFollow-up Team",
        now=NOW,
        policy=load_policy(POLICY_PATH),
    )
    rejected = reject_draft(
        postgres_connection,
        draft_id=edited.id,
        expected_review_token=edited.review_token,
        now=NOW,
    )

    assert edited.body == "Hi Jamie, following up.\n\nFollow-up Team"
    assert rejected.status == "rejected"
    assert (
        postgres_connection.execute(text("SELECT COUNT(*) FROM outbox")).scalar_one()
        == 0
    )


def test_approval_validation_error_is_reported_as_blocked(postgres_connection):
    key = "crm:invalid-approval"
    address = "+13125550114"
    opportunity_id = "REVIEW-INVALID-APPROVAL"
    _insert_opportunity(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address=address,
    )
    draft = _draft(
        postgres_connection,
        _insert_candidate(
            postgres_connection,
            opportunity_id=opportunity_id,
            contact_key=key,
            contact_address=address,
        ),
    )
    postgres_connection.execute(
        text("UPDATE drafts SET body = 'Call me about $100' WHERE id = :id"),
        {"id": draft.id},
    )

    draft = replace(draft, body="Call me about $100")
    with pytest.raises(ReviewBlockedError, match="failed validation"):
        approve_draft(
            postgres_connection,
            draft_id=draft.id,
            expected_review_token=draft.review_token,
            now=NOW,
            policy=load_policy(POLICY_PATH),
        )


def test_review_cannot_act_on_copy_changed_at_the_same_time(
    postgres_connection,
):
    key = "crm:concurrent-review"
    address = "jamie@example.com"
    opportunity_id = "REVIEW-CONCURRENT-EDIT"
    _insert_opportunity(
        postgres_connection,
        opportunity_id=opportunity_id,
        contact_key=key,
        contact_address=address,
        channel="email",
    )
    displayed = _draft(
        postgres_connection,
        _insert_candidate(
            postgres_connection,
            opportunity_id=opportunity_id,
            contact_key=key,
            contact_address=address,
            channel="email",
        ),
    )
    policy = load_policy(POLICY_PATH)
    edited = update_draft_message(
        postgres_connection,
        draft_id=displayed.id,
        expected_review_token=displayed.review_token,
        body="Hi Jamie, updated follow-up.\n\nFollow-up Team",
        now=NOW,
        policy=policy,
    )
    # Fixed --now and equal timestamp precision must not hide changed copy.
    assert edited.updated_at == displayed.updated_at
    assert edited.review_token != displayed.review_token

    with pytest.raises(ReviewBlockedError, match="changed since it was shown"):
        approve_draft(
            postgres_connection,
            draft_id=displayed.id,
            expected_review_token=displayed.review_token,
            now=NOW,
            policy=policy,
        )
    with pytest.raises(ReviewBlockedError, match="changed since it was shown"):
        update_draft_message(
            postgres_connection,
            draft_id=displayed.id,
            expected_review_token=displayed.review_token,
            body="Hi Jamie, another reviewer's edit.",
            now=NOW,
            policy=policy,
        )
    with pytest.raises(ReviewBlockedError, match="changed since it was shown"):
        reject_draft(
            postgres_connection,
            draft_id=displayed.id,
            expected_review_token=displayed.review_token,
            now=NOW,
        )
    assert (
        postgres_connection.execute(
            text("SELECT body FROM drafts WHERE id = :id"), {"id": displayed.id}
        ).scalar_one()
        == edited.body
    )
    assert (
        postgres_connection.execute(text("SELECT COUNT(*) FROM outbox")).scalar_one()
        == 0
    )

    approved = approve_draft(
        postgres_connection,
        draft_id=edited.id,
        expected_review_token=edited.review_token,
        now=NOW,
        policy=policy,
    )
    assert (
        postgres_connection.execute(
            text("SELECT body FROM outbox WHERE id = :id"), {"id": approved}
        ).scalar_one()
        == edited.body
    )
    # A retry with the reviewed version remains idempotent, while an old
    # reviewer cannot report that their superseded version was approved.
    assert (
        approve_draft(
            postgres_connection,
            draft_id=edited.id,
            expected_review_token=edited.review_token,
            now=NOW,
            policy=policy,
        )
        == approved
    )
    with pytest.raises(ReviewBlockedError, match="changed since it was shown"):
        approve_draft(
            postgres_connection,
            draft_id=displayed.id,
            expected_review_token=displayed.review_token,
            now=NOW,
            policy=policy,
        )


def test_candidate_drafts_are_lazy_and_follow_score_order(postgres_connection):
    run_id = uuid4()
    postgres_connection.execute(
        text(
            "INSERT INTO sync_runs (id, run_at, candidate_count) "
            "VALUES (:id, :run_at, 2)"
        ),
        {"id": run_id, "run_at": NOW + timedelta(minutes=1)},
    )
    for opportunity_id, key, address, name, score in (
        ("REVIEW-LOW", "crm:low", "+13125550104", "Low Priority", "10"),
        ("REVIEW-HIGH", "crm:high", "+13125550105", "High Priority", "100"),
    ):
        _insert_opportunity(
            postgres_connection,
            opportunity_id=opportunity_id,
            contact_key=key,
            contact_address=address,
            contact_name=name,
        )
        _insert_candidate(
            postgres_connection,
            opportunity_id=opportunity_id,
            contact_key=key,
            contact_address=address,
            contact_name=name,
            score=score,
            run_id=run_id,
        )

    calls = []
    drafts = iter_candidate_drafts(
        postgres_connection,
        candidates=load_latest_candidates(postgres_connection),
        now=NOW,
        policy=load_policy(POLICY_PATH),
        adapter=_adapter(calls),
    )
    first = next(drafts)

    assert first.primary_opportunity_id == "REVIEW-HIGH"
    assert len(calls) == 1
    assert "High Priority" in calls[0][1]["content"]
    assert (
        postgres_connection.execute(text("SELECT COUNT(*) FROM drafts")).scalar_one()
        == 1
    )

@pytest.mark.parametrize("source", ["outbox_after_captured_now", "future_snapshot"])
def test_approval_preserves_cooldown_despite_future_timestamps(postgres_connection, source):
    key = f"crm:clock:{source}"
    opportunity_id = f"clock-{source}"
    _insert_opportunity(
        postgres_connection, opportunity_id=opportunity_id,
        contact_key=key, contact_address="clock@example.com", channel="email",
    )
    draft = _draft(postgres_connection, _insert_candidate(
        postgres_connection, opportunity_id=opportunity_id,
        contact_key=key, contact_address="clock@example.com", channel="email",
    ))
    if source == "future_snapshot":
        postgres_connection.execute(
            text("UPDATE opportunities SET last_contact_at = :at WHERE id = :id"),
            {"at": NOW + timedelta(days=1), "id": opportunity_id},
        )
        postgres_connection.execute(
            text("INSERT INTO activities (id, type, opportunity_id, occurred_at, direction) "
                 "VALUES (:id, 'message_sent', :id, :at, 'outbound')"),
            {"id": opportunity_id, "at": NOW - timedelta(hours=1)},
        )
    else:
        other = _draft(postgres_connection, _insert_candidate(
            postgres_connection, opportunity_id=opportunity_id,
            contact_key=key, contact_address="clock@example.com", channel="email",
        ))
        # Simulate a reservation committed while this review waited for a lock.
        enqueue_outbox(
            postgres_connection, draft_id=other.id, contact_key=key,
            contact_address=other.contact_address, contact_name=other.contact_name,
            channel=other.channel, opportunity_ids=other.opportunity_ids,
            body=other.body, created_at=NOW + timedelta(seconds=1),
        )
    with pytest.raises(ReviewBlockedError, match="cooldown"):
        approve_draft(
            postgres_connection, draft_id=draft.id,
            expected_review_token=draft.review_token, now=NOW,
            policy=load_policy(POLICY_PATH),
        )
    assert postgres_connection.execute(
        text("SELECT COUNT(*) FROM outbox WHERE draft_id = :id"),
        {"id": draft.id},
    ).scalar_one() == 0


@pytest.mark.parametrize("changes", [
    {"body": "Changed copy"}, {"contact_address": "another@example.com"},
    {"contact_key": "another-person"}, {"channel": "email"},
    {"opportunity_ids": ("another-opportunity",)},
])
def test_review_token_binds_copy_to_its_recipient_and_opportunities(changes):
    from respawned.core.review import PersistedDraft

    original = PersistedDraft(
        id=uuid4(), candidate_id=uuid4(), contact_key="person:one", contact_address="+13125550123",
        contact_name="Jamie", channel="sms", primary_opportunity_id="one", opportunity_ids=("one",),
        body="Hi Jamie, checking in.", status="pending", created_at=NOW, updated_at=NOW, reviewed_at=None,
    )
    changed = replace(original, **changes)
    assert changed.updated_at == original.updated_at
    assert changed.review_token != original.review_token


def test_outbox_cooldown_uses_one_bounded_exists_query():
    from respawned.core.review import _has_outbox_reservation

    observed = {}

    class Result:
        def scalar_one(self):
            return True

    class Connection:
        def execute(self, statement, parameters):
            observed["sql"] = " ".join(str(statement).split())
            observed["parameters"] = parameters
            return Result()

    draft_id = uuid4()
    assert _has_outbox_reservation(Connection(), draft_id=draft_id, contact_key="crm:contact:1",
                                  now=NOW, cooldown_hours=Decimal(72))
    assert "SELECT EXISTS" in observed["sql"]
    assert "created_at >" in observed["sql"] and "created_at <=" not in observed["sql"]
    assert observed["parameters"] == {"draft_id": draft_id, "contact_key": "crm:contact:1",
                                      "now": NOW, "cooldown_seconds": Decimal(259200)}
