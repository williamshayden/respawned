from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from respawned.core.context import BusinessContext
from respawned.core.domain import OpportunityState
from respawned.core.policy import (
    Policy,
    RankingPolicy,
    ReasonPolicy,
    load_policy,
)
from respawned.core.reasons import ReasonMatch
from respawned.core.review import (
    approve_draft,
    draft_candidate,
    load_latest_candidates,
    reject_draft,
)
from respawned.core.sync import compute_candidates, sync_candidates
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
DEFAULT_POLICY_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "respawned"
    / "config"
    / "policy.yaml"
)


def _replied_state(
    opportunity_id: str,
    contact_key: str,
    value: int,
) -> OpportunityState:
    return OpportunityState(
        opportunity_id=opportunity_id,
        status="open",
        value=Decimal(value),
        contact_key=contact_key,
        contact_name=f"Contact {opportunity_id}",
        contact_email=f"{opportunity_id}@example.com",
        owner_name="Owner",
        created_at=NOW - timedelta(days=2),
        last_replied_at=NOW - timedelta(hours=1),
    )


def _replied_only_policy() -> Policy:
    return Policy(
        business_context=BusinessContext("UTC"),
        cooldown_hours=Decimal("72"),
        dead_after_days=45,
        ranking=RankingPolicy(
            value_weight=Decimal("100"),
            signal_weight=Decimal("0"),
        ),
        reasons={
            "replied_no_answer": ReasonPolicy(
                evaluator="reply_after_outbound",
                base_score=Decimal("10"),
                tone="warm",
                params={"horizon_days": 7},
            )
        },
    )


def _insert_replied_opportunity(
    connection,
    opportunity_id: str,
    *,
    channel: str = "sms",
    contact_name: str = "Sync Contact",
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO opportunities (
                id, contact_key, contact_name, contact_phone, contact_email,
                owner_name, value, status, created_at, last_contact_at,
                preferred_channel
            ) VALUES (
                :id, :contact_key, :contact_name, :contact_phone, :contact_email,
                'Owner', 99999, 'open', :created_at, NULL, :channel
            )
            """
        ),
        {
            "id": opportunity_id,
            "contact_key": f"contact:{opportunity_id}",
            "contact_name": contact_name,
            "contact_phone": "+13125559999",
            "contact_email": "contact@example.com",
            "created_at": NOW - timedelta(days=2),
            "channel": channel,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO activities (
                id, type, opportunity_id, occurred_at, channel, direction
            ) VALUES (
                :created_id, 'opportunity_created', :opportunity_id,
                :created_at, NULL, NULL
            ), (
                :reply_id, 'contact_replied', :opportunity_id,
                :reply_at, :channel, 'inbound'
            )
            """
        ),
        {
            "created_id": str(uuid4()),
            "reply_id": str(uuid4()),
            "opportunity_id": opportunity_id,
            "channel": channel,
            "created_at": NOW - timedelta(days=2),
            "reply_at": NOW - timedelta(hours=1),
        },
    )


def _table_count(connection, table: str) -> int:
    return connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def test_top_ten_limit_is_applied_after_contact_grouping():
    states = [
        _replied_state(f"shared-{index}", "shared-contact", 1000 - index)
        for index in range(3)
    ]
    states.extend(
        _replied_state(
            f"unique-{index:02d}",
            f"contact-{index:02d}",
            900 - index,
        )
        for index in range(10)
    )

    candidates = compute_candidates(
        states,
        _replied_only_policy(),
        NOW,
        limit=10,
    )

    assert len(candidates) == 10
    assert len({candidate.contact_key for candidate in candidates}) == 10
    assert candidates[0].primary_opportunity_id == "shared-0"


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_limit_must_be_a_non_negative_integer(limit):
    with pytest.raises(ValueError, match="non-negative integer"):
        compute_candidates([], _replied_only_policy(), NOW, limit=limit)


def test_compute_candidates_forwards_custom_evaluators():
    policy = Policy(
        business_context=BusinessContext("UTC"),
        cooldown_hours=Decimal("72"),
        dead_after_days=45,
        ranking=RankingPolicy(Decimal("0"), Decimal("0")),
        reasons={
            "custom": ReasonPolicy("always", Decimal("10"), "warm", {})
        },
    )

    def always(_params):
        return lambda context: ReasonMatch(Decimal("1"), context.now)

    candidates = compute_candidates(
        [_replied_state("custom", "custom-contact", 1)],
        policy,
        NOW,
        evaluators={"always": always},
    )

    assert candidates[0].reason == "custom"


def test_two_unchanged_syncs_create_no_duplicate_candidates(postgres_connection):
    _insert_replied_opportunity(postgres_connection, "sync-idempotent")
    policy = load_policy(DEFAULT_POLICY_PATH)

    first = sync_candidates(postgres_connection, now=NOW, policy=policy)
    after_first = _table_count(postgres_connection, "candidates")
    second = sync_candidates(postgres_connection, now=NOW, policy=policy)
    dry_run = sync_candidates(
        postgres_connection,
        now=NOW,
        policy=policy,
        dry_run=True,
    )

    assert first.inserted_count == len(first.candidates) > 0
    assert second.inserted_count == 0
    assert dry_run.inserted_count == 0
    assert _table_count(postgres_connection, "candidates") == after_first


def test_unchanged_candidate_is_linked_to_latest_sync_run(postgres_connection):
    _insert_replied_opportunity(postgres_connection, "sync-latest-run")
    policy = load_policy(DEFAULT_POLICY_PATH)

    first = sync_candidates(postgres_connection, now=NOW, policy=policy)
    second = sync_candidates(postgres_connection, now=NOW, policy=policy)
    linked_run_id = postgres_connection.execute(
        text("SELECT sync_run_id FROM candidates WHERE id = :candidate_id"),
        {"candidate_id": first.candidates[0].id},
    ).scalar_one()

    assert second.inserted_count == 0
    assert linked_run_id == second.run_id


def test_backdated_sync_cannot_steal_membership_or_overwrite_newer_snapshot(
    postgres_connection,
):
    opportunity_id = "sync-out-of-order"
    _insert_replied_opportunity(
        postgres_connection,
        opportunity_id,
        contact_name="First Contact",
    )
    policy = load_policy(DEFAULT_POLICY_PATH)

    first = sync_candidates(postgres_connection, now=NOW, policy=policy)
    candidate_id = first.candidates[0].id
    postgres_connection.execute(
        text("UPDATE opportunities SET contact_name = 'Backdated Contact' WHERE id = :id"),
        {"id": opportunity_id},
    )
    backdated = sync_candidates(
        postgres_connection,
        now=NOW - timedelta(minutes=15),
        policy=policy,
    )
    after_backdated = postgres_connection.execute(
        text(
            """
            SELECT sync_run_id, run_at, contact_name, score
            FROM candidates WHERE id = :candidate_id
            """
        ),
        {"candidate_id": candidate_id},
    ).mappings().one()

    assert backdated.inserted_count == 0
    assert backdated.candidates[0].contact_name == "Backdated Contact"
    assert after_backdated["sync_run_id"] == first.run_id
    assert after_backdated["run_at"] == NOW
    assert after_backdated["contact_name"] == "First Contact"
    assert after_backdated["score"] == first.candidates[0].score

    postgres_connection.execute(
        text("UPDATE opportunities SET contact_name = 'Newest Contact' WHERE id = :id"),
        {"id": opportunity_id},
    )
    newest = sync_candidates(
        postgres_connection,
        now=NOW + timedelta(minutes=15),
        policy=policy,
    )
    after_newest = postgres_connection.execute(
        text(
            """
            SELECT sync_run_id, run_at, contact_name, score
            FROM candidates WHERE id = :candidate_id
            """
        ),
        {"candidate_id": candidate_id},
    ).mappings().one()

    assert newest.inserted_count == 0
    assert after_newest["sync_run_id"] == newest.run_id
    assert after_newest["run_at"] == NOW + timedelta(minutes=15)
    assert after_newest["contact_name"] == "Newest Contact"
    assert after_newest["score"] == newest.candidates[0].score


def test_dry_run_computes_candidates_without_writing(postgres_connection):
    _insert_replied_opportunity(postgres_connection, "sync-dry-run")
    before_candidates = _table_count(postgres_connection, "candidates")
    before_runs = _table_count(postgres_connection, "sync_runs")

    result = sync_candidates(
        postgres_connection,
        now=NOW,
        policy=load_policy(DEFAULT_POLICY_PATH),
        dry_run=True,
    )

    assert result.candidates
    assert result.inserted_count == len(result.candidates)
    assert _table_count(postgres_connection, "candidates") == before_candidates
    assert _table_count(postgres_connection, "sync_runs") == before_runs


def test_sync_persists_generic_email_route_and_contact_name(postgres_connection):
    opportunity_id = "sync-email"
    _insert_replied_opportunity(
        postgres_connection,
        opportunity_id,
        channel="email",
        contact_name="Email Contact",
    )

    sync_candidates(
        postgres_connection,
        now=NOW,
        policy=load_policy(DEFAULT_POLICY_PATH),
    )
    row = postgres_connection.execute(
        text(
            """
            SELECT primary_opportunity_id, contact_key, contact_address,
                   contact_name, channel
            FROM candidates
            WHERE primary_opportunity_id = :opportunity_id
            """
        ),
        {"opportunity_id": opportunity_id},
    ).mappings().one()

    assert dict(row) == {
        "primary_opportunity_id": opportunity_id,
        "contact_key": f"contact:{opportunity_id}",
        "contact_address": "contact@example.com",
        "contact_name": "Email Contact",
        "channel": "email",
    }


def _draft_for_sync(connection, candidate, policy):
    return draft_candidate(
        connection,
        candidate=candidate,
        now=NOW,
        policy=policy,
        adapter=LiteLLMAdapter(
            proxy_url="http://proxy.test:4000",
            master_key="sk-test",
            model_alias="follow-up-model",
            completion_fn=lambda **_kwargs: {
                "choices": [{"message": {"content": "Hello, checking in."}}]
            },
        ),
    )


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_reviewed_top_ten_do_not_starve_the_next_contact(
    postgres_connection, decision
):
    policy = _replied_only_policy()
    for index in range(11):
        _insert_replied_opportunity(postgres_connection, f"queue-{index:02d}")
    first = sync_candidates(postgres_connection, now=NOW, policy=policy)
    assert len(first.candidates) == 10

    for candidate in first.candidates:
        draft = _draft_for_sync(postgres_connection, candidate, policy)
        if decision == "approve":
            approve_draft(
                postgres_connection,
                draft_id=draft.id,
                expected_review_token=draft.review_token,
                now=NOW,
                policy=policy,
            )
        else:
            reject_draft(
                postgres_connection,
                draft_id=draft.id,
                expected_review_token=draft.review_token,
                now=NOW,
            )

    before_runs = _table_count(postgres_connection, "sync_runs")
    before_candidates = _table_count(postgres_connection, "candidates")
    preview = sync_candidates(
        postgres_connection, now=NOW, policy=policy, dry_run=True
    )
    assert [item.primary_opportunity_id for item in preview.candidates] == ["queue-10"]
    assert preview.inserted_count == 1
    assert _table_count(postgres_connection, "sync_runs") == before_runs
    assert _table_count(postgres_connection, "candidates") == before_candidates

    next_run = sync_candidates(postgres_connection, now=NOW, policy=policy)
    assert next_run.candidates == preview.candidates
    assert next_run.inserted_count == 1
    assert load_latest_candidates(postgres_connection) == list(next_run.candidates)


def test_pending_draft_keeps_its_place_and_rejection_only_suppresses_same_evidence(
    postgres_connection,
):
    policy = _replied_only_policy()
    _insert_replied_opportunity(postgres_connection, "queue-pending")
    candidate = sync_candidates(
        postgres_connection, now=NOW, policy=policy
    ).candidates[0]
    draft = _draft_for_sync(postgres_connection, candidate, policy)
    repeated = sync_candidates(
        postgres_connection, now=NOW, policy=policy, dry_run=True
    )
    assert repeated.candidates == (candidate,)
    assert repeated.inserted_count == 0
    reject_draft(
        postgres_connection,
        draft_id=draft.id,
        expected_review_token=draft.review_token,
        now=NOW,
    )
    assert not sync_candidates(
        postgres_connection, now=NOW, policy=policy, dry_run=True
    ).candidates

    postgres_connection.execute(
        text(
            """
            INSERT INTO activities (id, type, opportunity_id, occurred_at, direction)
            VALUES ('new-reply', 'contact_replied', 'queue-pending', :now, 'inbound')
            """
        ),
        {"now": NOW},
    )
    new_evidence = sync_candidates(
        postgres_connection, now=NOW, policy=policy, dry_run=True
    )
    assert len(new_evidence.candidates) == 1
    assert new_evidence.candidates[0].id != candidate.id


@pytest.mark.parametrize("elapsed_hours", [0, 71, 72, -0.25])
def test_reservations_suppress_new_evidence_until_contact_cooldown_expires(
    postgres_connection, elapsed_hours
):
    policy = _replied_only_policy()
    _insert_replied_opportunity(postgres_connection, "queue-reserved")
    candidate = sync_candidates(
        postgres_connection, now=NOW, policy=policy
    ).candidates[0]
    draft = _draft_for_sync(postgres_connection, candidate, policy)
    approve_draft(
        postgres_connection,
        draft_id=draft.id,
        expected_review_token=draft.review_token,
        now=NOW,
        policy=policy,
    )
    # A new reply changes the candidate ID but cannot bypass the contact cooldown.
    postgres_connection.execute(
        text(
            """
            INSERT INTO activities (id, type, opportunity_id, occurred_at, direction)
            VALUES ('reservation-reply', 'contact_replied', 'queue-reserved',
                    :replied_at, 'inbound')
            """
        ),
        {"replied_at": NOW - timedelta(minutes=30)},
    )
    # A backdated evaluation must also respect newer, already reserved work.
    as_of = NOW + timedelta(hours=elapsed_hours)
    result = sync_candidates(
        postgres_connection, now=as_of, policy=policy, dry_run=True
    )
    assert bool(result.candidates) is (elapsed_hours == 72)
    if result.candidates:
        assert result.candidates[0].id != candidate.id


def test_sync_persists_in_stable_order_without_changing_returned_ranking(monkeypatch):
    from respawned.core import sync

    states = [
        _replied_state("first", "contact-first", 2),
        _replied_state("second", "contact-second", 1),
    ]
    expected = compute_candidates(states, _replied_only_policy(), NOW)
    monkeypatch.setattr(sync, "reduce_opportunities", lambda *_args: states)
    inserted_ids = []

    class Result:
        def mappings(self):
            return []

        def scalars(self):
            return []

        def scalar_one_or_none(self):
            return uuid4()

    class Connection:
        def execute(self, statement, parameters):
            if statement is sync.INSERT_CANDIDATE:
                inserted_ids.append(parameters["id"])
            return Result()

    result = sync_candidates(Connection(), now=NOW, policy=_replied_only_policy())
    assert inserted_ids == sorted(candidate.id for candidate in expected)
    assert result.candidates == tuple(expected)
