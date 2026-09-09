from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from respawned.cli.outbox import enqueue_outbox
from respawned.core.candidates import Candidate
from respawned.core.score import load_policy
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
DEFAULT_POLICY_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "respawned"
    / "config"
    / "policy.yaml"
)


def _adapter(calls: list[list[dict[str, str]]]) -> LiteLLMAdapter:
    def fake_completion(**kwargs):
        calls.append(kwargs["messages"])
        return {
            "choices": [
                {
                    "message": {
                        "content": "Hi Jamie, just checking in on your quote."
                    }
                }
            ]
        }

    return LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=fake_completion,
    )


def _insert_quote(
    connection,
    *,
    quote_id: str,
    phone: str,
    customer_name: str = "Jamie",
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO quotes (
                id, customer_name, customer_phone, tech_name,
                amount, status, created_at, last_contact_at
            ) VALUES (
                :id, :customer_name, :phone, 'Sam',
                1250, 'open', :created_at, NULL
            )
            """
        ),
        {
            "id": quote_id,
            "customer_name": customer_name,
            "phone": phone,
            "created_at": NOW - timedelta(days=2),
        },
    )


def _insert_candidate(
    connection,
    *,
    quote_id: str,
    phone: str,
    score: str = "90",
    other_quote_ids: tuple[str, ...] = (),
    run_id=None,
    channel: str = "sms",
) -> Candidate:
    candidate = Candidate(
        id=uuid4(),
        run_at=NOW,
        primary_quote_id=quote_id,
        customer_phone=phone,
        reason="replied_no_answer",
        score=Decimal(score),
        other_quote_ids=other_quote_ids,
        channel=channel,
    )
    active_run_id = run_id or uuid4()
    if run_id is None:
        connection.execute(
            text(
                """
                INSERT INTO sync_runs (id, run_at, candidate_count)
                VALUES (:id, :run_at, 1)
                """
            ),
            {"id": active_run_id, "run_at": NOW},
        )
    connection.execute(
        text(
            """
            INSERT INTO candidates (
                id, sync_run_id, run_at, primary_quote_id,
                customer_phone, reason, score, other_quote_ids
            ) VALUES (
                :id, :sync_run_id, :run_at, :primary_quote_id,
                :customer_phone, :reason, :score, :other_quote_ids
            )
            """
        ),
        {
            "id": candidate.id,
            "sync_run_id": active_run_id,
            "run_at": candidate.run_at,
            "primary_quote_id": candidate.primary_quote_id,
            "customer_phone": candidate.customer_phone,
            "reason": candidate.reason,
            "score": candidate.score,
            "other_quote_ids": list(candidate.other_quote_ids),
        },
    )
    return candidate


def _draft(connection, *, candidate: Candidate):
    from respawned.cli.review import draft_candidate

    return draft_candidate(
        connection,
        candidate=candidate,
        now=NOW,
        policy=load_policy(DEFAULT_POLICY_PATH),
        adapter=_adapter([]),
    )


def test_approving_same_draft_twice_enqueues_one_sms(postgres_connection):
    from respawned.cli.review import approve_draft

    phone = "+1 (312) 555-0101"
    quote_id = "REVIEW-IDEMPOTENT"
    _insert_quote(postgres_connection, quote_id=quote_id, phone=phone)
    candidate = _insert_candidate(
        postgres_connection,
        quote_id=quote_id,
        phone=phone,
    )
    draft = _draft(postgres_connection, candidate=candidate)
    policy = load_policy(DEFAULT_POLICY_PATH)

    first_id = approve_draft(
        postgres_connection,
        draft_id=draft.id,
        now=NOW,
        policy=policy,
    )
    second_id = approve_draft(
        postgres_connection,
        draft_id=draft.id,
        now=NOW,
        policy=policy,
    )

    rows = postgres_connection.execute(
        text(
            """
            SELECT id, draft_id, body, channel, customer_phone
            FROM outbox
            WHERE draft_id = :draft_id
            """
        ),
        {"draft_id": str(draft.id)},
    ).all()
    assert first_id == second_id
    assert rows == [
        (first_id, str(draft.id), draft.body, "sms", phone),
    ]


def test_approving_email_candidate_enqueues_one_email(postgres_connection):
    from respawned.cli.review import approve_draft

    phone = "+1 (312) 555-0198"
    quote_id = "REVIEW-EMAIL"
    _insert_quote(postgres_connection, quote_id=quote_id, phone=phone)
    candidate = _insert_candidate(
        postgres_connection,
        quote_id=quote_id,
        phone=phone,
        channel="email",
    )
    draft = _draft(postgres_connection, candidate=candidate)

    outbox_id = approve_draft(
        postgres_connection,
        draft_id=draft.id,
        now=NOW,
        policy=load_policy(DEFAULT_POLICY_PATH),
    )

    channel = postgres_connection.execute(
        text("SELECT channel FROM outbox WHERE id = :id"),
        {"id": outbox_id},
    ).scalar_one()
    assert channel == "email"


@pytest.mark.parametrize("contact_source", ("quote_state", "outbox"))
def test_approval_blocks_cooldown_consumed_after_drafting(
    postgres_connection,
    contact_source,
):
    from respawned.cli.review import ReviewBlockedError, approve_draft

    phone = "+1 (312) 555-0102"
    quote_id = f"REVIEW-COOLDOWN-{contact_source}"
    _insert_quote(postgres_connection, quote_id=quote_id, phone=phone)
    candidate = _insert_candidate(
        postgres_connection,
        quote_id=quote_id,
        phone=phone,
    )
    draft = _draft(postgres_connection, candidate=candidate)

    if contact_source == "quote_state":
        postgres_connection.execute(
            text("UPDATE quotes SET last_contact_at = :at WHERE id = :id"),
            {"at": NOW - timedelta(hours=1), "id": quote_id},
        )
    else:
        enqueue_outbox(
            postgres_connection,
            draft_id="another-reservation",
            body="A separate pending follow-up",
            channel="sms",
            customer_phone="+13125550102",
            created_at=NOW - timedelta(hours=1),
        )

    with pytest.raises(ReviewBlockedError, match="cooldown"):
        approve_draft(
            postgres_connection,
            draft_id=draft.id,
            now=NOW,
            policy=load_policy(DEFAULT_POLICY_PATH),
        )

    assert postgres_connection.execute(
        text("SELECT COUNT(*) FROM outbox WHERE draft_id = :draft_id"),
        {"draft_id": str(draft.id)},
    ).scalar_one() == 0


def test_approval_blocks_when_a_mentioned_quote_is_no_longer_open(
    postgres_connection,
):
    from respawned.cli.review import ReviewBlockedError, approve_draft

    phone = "+13125550103"
    primary_id = "REVIEW-PRIMARY-OPEN"
    mentioned_id = "REVIEW-MENTIONED-CLOSED"
    _insert_quote(postgres_connection, quote_id=primary_id, phone=phone)
    _insert_quote(postgres_connection, quote_id=mentioned_id, phone=phone)
    candidate = _insert_candidate(
        postgres_connection,
        quote_id=primary_id,
        phone=phone,
        other_quote_ids=(mentioned_id,),
    )
    draft = _draft(postgres_connection, candidate=candidate)
    assert draft.quote_ids == (primary_id, mentioned_id)

    postgres_connection.execute(
        text("UPDATE quotes SET status = 'dismissed' WHERE id = :id"),
        {"id": mentioned_id},
    )

    with pytest.raises(ReviewBlockedError, match="no longer open"):
        approve_draft(
            postgres_connection,
            draft_id=draft.id,
            now=NOW,
            policy=load_policy(DEFAULT_POLICY_PATH),
        )


def test_candidate_drafts_are_generated_lazily_in_priority_order(
    postgres_connection,
):
    from respawned.cli.review import (
        iter_candidate_drafts,
        load_latest_candidates,
    )

    run_id = uuid4()
    postgres_connection.execute(
        text(
            """
            INSERT INTO sync_runs (id, run_at, candidate_count)
            VALUES (:id, :run_at, 2)
            """
        ),
        {"id": run_id, "run_at": NOW + timedelta(minutes=1)},
    )
    for quote_id, phone, customer_name, score in (
        ("REVIEW-LOW", "+13125550104", "Low Priority", "10"),
        ("REVIEW-HIGH", "+13125550105", "High Priority", "100"),
    ):
        _insert_quote(
            postgres_connection,
            quote_id=quote_id,
            phone=phone,
            customer_name=customer_name,
        )
        _insert_candidate(
            postgres_connection,
            quote_id=quote_id,
            phone=phone,
            score=score,
            run_id=run_id,
        )

    calls: list[list[dict[str, str]]] = []
    candidates = load_latest_candidates(postgres_connection)
    drafts = iter_candidate_drafts(
        postgres_connection,
        candidates=candidates,
        now=NOW,
        policy=load_policy(DEFAULT_POLICY_PATH),
        adapter=_adapter(calls),
    )

    first = next(drafts)

    assert first.primary_quote_id == "REVIEW-HIGH"
    assert len(calls) == 1
    assert "High Priority" in calls[0][1]["content"]
    assert postgres_connection.execute(
        text("SELECT COUNT(*) FROM drafts")
    ).scalar_one() == 1
