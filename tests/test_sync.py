from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from respawned.cli.review import load_latest_candidates
from respawned.cli.sync import compute_candidates, sync_candidates
from respawned.core.context import BusinessContext
from respawned.core.reduce import QuoteState
from respawned.core.score import Policy, ReasonPolicy, load_policy


NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
DEFAULT_POLICY_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "respawned"
    / "config"
    / "policy.yaml"
)


def _replied_state(
    quote_id: str,
    phone: str,
    amount: int,
) -> QuoteState:
    return QuoteState(
        quote_id=quote_id,
        status="open",
        amount=Decimal(amount),
        customer_name=f"Customer {quote_id}",
        customer_phone=phone,
        tech_name="Sam",
        created_at=NOW - timedelta(days=2),
        quote_sent_at=NOW - timedelta(days=2),
        last_viewed_at=None,
        view_days=0,
        last_replied_at=NOW - timedelta(hours=1),
        last_outbound_at=None,
    )


def _replied_only_policy() -> Policy:
    return Policy(
        business_context=BusinessContext("UTC"),
        cooldown_hours=Decimal("72"),
        dead_after_days=45,
        high_pct=Decimal("0.75"),
        reasons={
            "replied_no_answer": ReasonPolicy(
                base=Decimal("10"),
                amount_weight=Decimal("100"),
                recency_weight=Decimal("0"),
                priority=1,
                recency_days=Decimal("7"),
            )
        },
    )


def _insert_replied_quote(
    connection,
    quote_id: str,
    *,
    channel: str = "sms",
    customer_name: str = "Sync Customer",
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO quotes (
                id, customer_name, customer_phone, tech_name,
                amount, status, created_at, last_contact_at
            ) VALUES (
                :id, :customer_name, '+13125559999', 'Sam',
                99999, 'open', :created_at, NULL
            )
            """
        ),
        {
            "id": quote_id,
            "customer_name": customer_name,
            "created_at": NOW - timedelta(days=2),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO events (
                event_id, type, quote_id, "timestamp", channel, direction
            ) VALUES (
                :sent_id, 'quote_sent', :quote_id, :sent_at, :channel, 'outbound'
            ), (
                :reply_id, 'customer_replied', :quote_id, :reply_at, :channel, 'inbound'
            )
            """
        ),
        {
            "sent_id": uuid4(),
            "reply_id": uuid4(),
            "quote_id": quote_id,
            "channel": channel,
            "sent_at": NOW - timedelta(days=2),
            "reply_at": NOW - timedelta(hours=1),
        },
    )


def _table_count(connection, table: str) -> int:
    return connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def test_top_ten_limit_is_applied_after_customer_grouping():
    states = [
        _replied_state(f"shared-{index}", "+13125550000", 1000 - index)
        for index in range(3)
    ]
    states.extend(
        _replied_state(
            f"unique-{index:02d}",
            f"+13125551{index:03d}",
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
    assert len({candidate.customer_phone for candidate in candidates}) == 10
    assert candidates[0].primary_quote_id == "shared-0"


def test_two_unchanged_real_syncs_create_no_duplicate_opportunities(
    postgres_connection,
):
    _insert_replied_quote(postgres_connection, "SYNC-IDEMPOTENT-Q")
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

    assert first.inserted_count == len(first.candidates)
    assert first.inserted_count > 0
    assert second.inserted_count == 0
    assert dry_run.inserted_count == 0
    assert _table_count(postgres_connection, "candidates") == after_first


def test_unchanged_candidate_is_linked_to_latest_sync_run(postgres_connection):
    _insert_replied_quote(postgres_connection, "SYNC-LATEST-RUN-Q")
    policy = load_policy(DEFAULT_POLICY_PATH)

    first = sync_candidates(postgres_connection, now=NOW, policy=policy)
    second = sync_candidates(postgres_connection, now=NOW, policy=policy)
    candidate_id = first.candidates[0].id
    linked_run_id = postgres_connection.execute(
        text("SELECT sync_run_id FROM candidates WHERE id = :candidate_id"),
        {"candidate_id": candidate_id},
    ).scalar_one()

    assert second.inserted_count == 0
    assert linked_run_id == second.run_id


def test_dry_run_computes_candidates_without_writing(postgres_connection):
    _insert_replied_quote(postgres_connection, "SYNC-DRY-RUN-Q")
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


def test_sync_retains_channel_and_customer_name_for_review(postgres_connection):
    quote_id = "SYNC-EMAIL-CHANNEL-Q"
    _insert_replied_quote(
        postgres_connection,
        quote_id,
        channel="email",
        customer_name="Email Customer",
    )

    sync_candidates(
        postgres_connection,
        now=NOW,
        policy=load_policy(DEFAULT_POLICY_PATH),
    )
    loaded = next(
        candidate
        for candidate in load_latest_candidates(postgres_connection)
        if candidate.primary_quote_id == quote_id
    )

    assert loaded.channel == "email"
    assert loaded.customer_name == "Email Customer"
