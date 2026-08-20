from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from follow_up_engine.core.context import BusinessContext
from follow_up_engine.core.reduce import QuoteState
from follow_up_engine.core.score import (
    Policy,
    ReasonPolicy,
    load_policy,
    score_quotes,
)


NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
DEFAULT_POLICY_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "follow_up_engine"
    / "config"
    / "policy.yaml"
)


def _state(
    quote_id: str,
    *,
    status: str | None = "open",
    amount: Decimal | None = Decimal("1000"),
    phone: str | None = "+13125550100",
    created_at: datetime | None = NOW - timedelta(days=10),
    quote_sent_at: datetime | None = NOW - timedelta(days=10),
    last_viewed_at: datetime | None = NOW - timedelta(days=2),
    view_days: int = 1,
    last_replied_at: datetime | None = None,
    last_outbound_at: datetime | None = None,
    view_timestamps: tuple[datetime, ...] = (),
) -> QuoteState:
    return QuoteState(
        quote_id=quote_id,
        status=status,
        amount=amount,
        customer_name=f"Customer {quote_id}",
        customer_phone=phone,
        tech_name="Sam",
        created_at=created_at,
        quote_sent_at=quote_sent_at,
        last_viewed_at=last_viewed_at,
        view_days=view_days,
        last_replied_at=last_replied_at,
        last_outbound_at=last_outbound_at,
        view_timestamps=view_timestamps,
    )


def _default_policy() -> Policy:
    return load_policy(DEFAULT_POLICY_PATH)


def _reason_names(scored_quote) -> tuple[str, ...]:
    return tuple(item.reason for item in scored_quote.matched_reasons)


def test_default_policy_loads_scoring_and_drafting_settings():
    policy = _default_policy()

    assert policy.business_context == BusinessContext("UTC")
    assert policy.cooldown_hours == Decimal("72")
    assert policy.dead_after_days == 45
    assert policy.high_pct == Decimal("0.75")
    assert policy.drafting.sign_off == "Service Team"
    assert policy.drafting.max_characters == 320
    assert policy.drafting.require_tech_name is False
    assert set(policy.reasons) == {
        "replied_no_answer",
        "viewed_no_reply",
        "high_value_quiet",
        "repeat_views",
        "aging",
    }
    assert all(reason.tone.strip() for reason in policy.reasons.values())


def test_terminal_and_non_open_quotes_never_score():
    states = [
        _state("accepted", status="accepted"),
        _state("dismissed", status="dismissed"),
        _state("missing", status=None),
        _state("unknown", status="won"),
    ]

    assert score_quotes(states, _default_policy(), NOW) == []


def test_recent_direct_outbound_hard_suppresses_quote_even_after_reply():
    state = _state(
        "direct-cooldown",
        last_outbound_at=NOW - timedelta(hours=1),
        last_replied_at=NOW - timedelta(minutes=30),
    )

    assert score_quotes([state], _default_policy(), NOW) == []


def test_recent_terminal_sibling_outbound_suppresses_every_quote_for_phone():
    phone = "+13125550123"
    open_quote = _state(
        "open",
        phone=phone,
        last_replied_at=NOW - timedelta(minutes=30),
    )
    accepted_sibling = _state(
        "accepted-sibling",
        status="accepted",
        phone=f"  {phone}  ",
        last_outbound_at=NOW - timedelta(hours=1),
    )

    assert score_quotes(
        [open_quote, accepted_sibling],
        _default_policy(),
        NOW,
    ) == []


def test_high_value_uses_nearest_rank_and_includes_cutoff_ties():
    policy = _default_policy()
    quiet = {
        "created_at": NOW - timedelta(days=10),
        "quote_sent_at": NOW - timedelta(days=10),
        "last_viewed_at": None,
        "view_days": 0,
    }
    states = [
        _state("q10", amount=Decimal("10"), **quiet),
        _state("q20", amount=Decimal("20"), **quiet),
        _state("q30-a", amount=Decimal("30"), **quiet),
        _state("q30-b", amount=Decimal("30"), **quiet),
    ]

    scored = score_quotes(states, policy, NOW)

    assert [item.quote_id for item in scored] == ["q30-a", "q30-b"]
    assert {item.high_value_cutoff for item in scored} == {Decimal("30")}
    assert all(item.primary_reason == "high_value_quiet" for item in scored)


def test_high_value_cohort_is_built_before_cooldown_filtering():
    policy = replace(_default_policy(), high_pct=Decimal("0.70"))
    quiet = {
        "created_at": NOW - timedelta(days=10),
        "quote_sent_at": NOW - timedelta(days=10),
        "last_viewed_at": None,
        "view_days": 0,
    }
    states = [
        _state("q10", amount=Decimal("10"), **quiet),
        _state("q20-a", amount=Decimal("20"), **quiet),
        _state("q20-b", amount=Decimal("20"), **quiet),
        _state("q30", amount=Decimal("30"), **quiet),
        _state(
            "q100-cooldown",
            amount=Decimal("100"),
            phone="+13125550999",
            last_outbound_at=NOW - timedelta(hours=1),
            **quiet,
        ),
    ]

    scored = score_quotes(states, policy, NOW)

    assert [item.quote_id for item in scored] == ["q30"]
    assert scored[0].high_value_cutoff == Decimal("30")


def test_one_primary_reason_keeps_repeat_views_after_customer_reply():
    state = _state(
        "overlap",
        last_outbound_at=NOW - timedelta(days=4),
        last_viewed_at=NOW - timedelta(days=1),
        view_days=2,
        last_replied_at=NOW - timedelta(hours=12),
        view_timestamps=(NOW - timedelta(days=2), NOW - timedelta(days=1)),
    )

    [scored] = score_quotes([state], _default_policy(), NOW)

    assert scored.primary_reason == "replied_no_answer"
    assert _reason_names(scored) == (
        "replied_no_answer",
        "repeat_views",
    )
    assert scored.score == (
        scored.base_factor
        + scored.amount_factor
        + scored.recency_factor
    )


def test_repeat_views_counts_only_distinct_business_dates_after_outbound():
    outbound = NOW - timedelta(days=4)
    one_after = replace(
        _state(
            "one-after",
            last_outbound_at=outbound,
            last_viewed_at=NOW - timedelta(days=2),
            view_days=2,
        ),
        view_timestamps=(
            NOW - timedelta(days=5),
            NOW - timedelta(days=2),
        ),
    )
    two_after = replace(
        _state(
            "two-after",
            last_outbound_at=outbound,
            last_viewed_at=NOW - timedelta(days=2),
            view_days=2,
        ),
        view_timestamps=(
            NOW - timedelta(days=3),
            NOW - timedelta(days=2),
        ),
    )

    scored = {
        item.quote_id: item
        for item in score_quotes([one_after, two_after], _default_policy(), NOW)
    }

    assert "repeat_views" not in _reason_names(scored["one-after"])
    assert "repeat_views" in _reason_names(scored["two-after"])


def test_view_reasons_require_a_view_after_latest_customer_outbound():
    state = _state(
        "stale-views",
        last_viewed_at=NOW - timedelta(days=5),
        view_days=2,
        last_outbound_at=NOW - timedelta(days=4),
    )

    [scored] = score_quotes([state], _default_policy(), NOW)

    assert "viewed_no_reply" not in _reason_names(scored)
    assert "repeat_views" not in _reason_names(scored)


def test_dead_after_days_uses_configured_business_calendar_dates():
    origin = datetime(2026, 8, 20, 4, 45, tzinfo=UTC)
    now = datetime(2026, 8, 20, 5, 30, tzinfo=UTC)
    aging_only = {
        "aging": ReasonPolicy(
            base=Decimal("10"),
            amount_weight=Decimal("0"),
            recency_weight=Decimal("0"),
            priority=1,
            aging_days=0,
            recency_days=1,
        )
    }
    base_policy = _default_policy()
    utc_policy = replace(
        base_policy,
        business_context=BusinessContext("UTC"),
        dead_after_days=1,
        reasons=aging_only,
    )
    chicago_policy = replace(
        utc_policy,
        business_context=BusinessContext("America/Chicago"),
    )
    state = _state(
        "calendar-boundary",
        created_at=origin,
        quote_sent_at=origin,
        last_viewed_at=None,
        view_days=0,
    )

    assert [item.quote_id for item in score_quotes([state], utc_policy, now)] == [
        "calendar-boundary"
    ]
    assert score_quotes([state], chicago_policy, now) == []
