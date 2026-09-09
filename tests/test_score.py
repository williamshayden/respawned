from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from respawned.core.context import BusinessContext
from respawned.core.domain import OpportunityState
from respawned.core.policy import Policy, RankingPolicy, ReasonPolicy, load_policy
from respawned.core.reasons import ReasonMatch
from respawned.core.score import score_opportunities


NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
DEFAULT_POLICY_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "respawned"
    / "config"
    / "policy.yaml"
)


def _state(
    opportunity_id: str,
    *,
    status: str | None = "open",
    value: Decimal | None = Decimal("1000"),
    contact_key: str | None = "contact-1",
    created_at: datetime | None = NOW - timedelta(days=10),
    last_viewed_at: datetime | None = NOW - timedelta(days=2),
    last_replied_at: datetime | None = None,
    last_outbound_at: datetime | None = None,
    view_timestamps: tuple[datetime, ...] = (),
) -> OpportunityState:
    return OpportunityState(
        opportunity_id=opportunity_id,
        status=status,
        value=value,
        contact_key=contact_key,
        contact_name=f"Contact {opportunity_id}",
        contact_phone="+13125550100",
        owner_name="Sam",
        created_at=created_at,
        last_viewed_at=last_viewed_at,
        last_replied_at=last_replied_at,
        last_outbound_at=last_outbound_at,
        view_timestamps=view_timestamps,
    )


def _default_policy() -> Policy:
    return load_policy(DEFAULT_POLICY_PATH)


def _policy(
    reasons: dict[str, ReasonPolicy],
    *,
    value_weight: str = "20",
    signal_weight: str = "20",
) -> Policy:
    return Policy(
        business_context=BusinessContext("UTC"),
        cooldown_hours=Decimal("72"),
        dead_after_days=45,
        ranking=RankingPolicy(Decimal(value_weight), Decimal(signal_weight)),
        reasons=reasons,
    )


def _fixed_factory(params):
    strength = Decimal(str(params["strength"]))

    def evaluate(context):
        return ReasonMatch(strength, context.now - timedelta(hours=1))

    return evaluate


def _reason_names(scored_opportunity) -> tuple[str, ...]:
    return tuple(item.reason for item in scored_opportunity.reasons)


def test_global_formula_applies_value_once_after_reason_selection():
    policy = _policy(
        {
            "signal": ReasonPolicy(
                evaluator="fixed", base_score=Decimal("10"), tone="warm", params={"strength": ".25"}
            )
        },
        value_weight="8",
        signal_weight="20",
    )

    [scored] = score_opportunities(
        [_state("formula")], policy, NOW, evaluators={"fixed": _fixed_factory}
    )

    assert scored.value_percentile == Decimal(".5")
    assert scored.reasons[0].score == Decimal("15")
    assert scored.score == Decimal("19")


def test_base_is_a_prior_signal_can_change_reason_order_and_ties_are_lexical():
    policy = _policy(
        {
            "z_high_base": ReasonPolicy("fixed", Decimal("20"), "warm", {"strength": "0"}),
            "b_strong": ReasonPolicy("fixed", Decimal("10"), "warm", {"strength": "1"}),
            "a_strong": ReasonPolicy("fixed", Decimal("10"), "warm", {"strength": "1"}),
        },
        value_weight="0",
    )

    [scored] = score_opportunities(
        [_state("ordering")], policy, NOW, evaluators={"fixed": _fixed_factory}
    )

    assert _reason_names(scored) == ("a_strong", "b_strong", "z_high_base")
    assert scored.reasons[0].score == Decimal("30")
    assert scored.reasons[-1].score == Decimal("20")


def test_value_changes_opportunity_rank_not_primary_reason():
    policy = _policy(
        {"signal": ReasonPolicy("fixed", Decimal("10"), "warm", {"strength": ".5"})}
    )
    scored = score_opportunities(
        [_state("low", value=Decimal("10")), _state("high", value=Decimal("100"))],
        policy,
        NOW,
        evaluators={"fixed": _fixed_factory},
    )

    assert {_reason_names(item) for item in scored} == {("signal",)}
    assert {item.reasons[0].score for item in scored} == {Decimal("20")}
    by_id = {item.opportunity_id: item for item in scored}
    assert by_id["high"].score > by_id["low"].score


def test_custom_aliases_share_an_injected_evaluator_without_registration():
    policy = _policy(
        {
            "first_alias": ReasonPolicy("custom", Decimal("1"), "one", {"strength": ".1"}),
            "second_alias": ReasonPolicy("custom", Decimal("2"), "two", {"strength": ".2"}),
        }
    )

    [scored] = score_opportunities(
        [_state("custom")], policy, NOW, evaluators={"custom": _fixed_factory}
    )

    assert _reason_names(scored) == ("second_alias", "first_alias")


def test_unknown_evaluator_fails_even_for_empty_snapshot():
    policy = _policy({"custom": ReasonPolicy("missing", Decimal("1"), "tone")})

    with pytest.raises(ValueError, match="Unknown reason evaluators.*missing"):
        score_opportunities([], policy, NOW)


def test_custom_evaluator_cannot_return_a_future_signal():
    policy = _policy({"custom": ReasonPolicy("future", Decimal("1"), "tone")})

    def future_factory(params):
        return lambda context: ReasonMatch(
            Decimal("1"), context.now + timedelta(seconds=1)
        )

    with pytest.raises(ValueError, match="future signal"):
        score_opportunities(
            [_state("future-signal")],
            policy,
            NOW,
            evaluators={"future": future_factory},
        )


def test_terminal_and_non_open_opportunities_never_score():
    states = [
        _state("accepted", status="accepted"),
        _state("dismissed", status="dismissed"),
        _state("missing", status=None),
        _state("unknown", status="won"),
    ]

    assert score_opportunities(states, _default_policy(), NOW) == []


def test_recent_direct_outbound_hard_suppresses_opportunity_even_after_reply():
    state = _state(
        "direct-cooldown",
        last_outbound_at=NOW - timedelta(hours=1),
        last_replied_at=NOW - timedelta(minutes=30),
    )

    assert score_opportunities([state], _default_policy(), NOW) == []


def test_direct_cooldown_is_enforced_even_without_a_contact_key():
    state = _state(
        "unidentified",
        contact_key=None,
        last_outbound_at=NOW - timedelta(hours=1),
        last_replied_at=NOW - timedelta(minutes=30),
    )

    assert score_opportunities([state], _default_policy(), NOW) == []


def test_recent_terminal_sibling_outbound_suppresses_every_opportunity_for_contact():
    contact_key = "contact-23"
    states = [
        _state(
            "open",
            contact_key=contact_key,
            last_replied_at=NOW - timedelta(minutes=30),
        ),
        _state(
            "accepted-sibling",
            status="accepted",
            contact_key=contact_key,
            last_outbound_at=NOW - timedelta(hours=1),
        ),
    ]

    assert score_opportunities(states, _default_policy(), NOW) == []


def test_shared_destination_does_not_share_cooldown_across_contact_keys():
    states = [
        _state(
            "recent-contact",
            contact_key="contact-1",
            last_outbound_at=NOW - timedelta(hours=1),
        ),
        _state(
            "other-contact",
            contact_key="contact-2",
            last_replied_at=NOW - timedelta(minutes=30),
        ),
    ]

    scored = score_opportunities(states, _default_policy(), NOW)

    assert [item.opportunity_id for item in scored] == ["other-contact"]


def test_future_origin_does_not_enter_value_cohort_or_score():
    quiet = {"last_viewed_at": None}
    states = [
        _state(
            "current-low",
            value=Decimal("10"),
            created_at=NOW - timedelta(days=10),
            **quiet,
        ),
        _state(
            "current-high",
            value=Decimal("20"),
            created_at=NOW - timedelta(days=10),
            **quiet,
        ),
        _state(
            "future",
            value=Decimal("100"),
            created_at=NOW + timedelta(days=1),
            **quiet,
        ),
    ]

    scored = {
        item.opportunity_id: item
        for item in score_opportunities(states, _default_policy(), NOW)
    }

    assert "future" not in scored
    assert scored["current-high"].reasons[0].reason == "high_value_quiet"


def test_future_outbound_does_not_suppress_an_as_of_reply():
    state = _state(
        "future-outbound",
        last_outbound_at=NOW + timedelta(hours=1),
        last_replied_at=NOW - timedelta(hours=1),
    )

    [scored] = score_opportunities([state], _default_policy(), NOW)

    assert scored.reasons[0].reason == "replied_no_answer"


def test_high_value_uses_nearest_rank_and_includes_cutoff_ties():
    quiet = {
        "created_at": NOW - timedelta(days=10),
        "last_viewed_at": None,
    }
    states = [
        _state("q10", value=Decimal("10"), **quiet),
        _state("q20", value=Decimal("20"), **quiet),
        _state("q30-a", value=Decimal("30"), **quiet),
        _state("q30-b", value=Decimal("30"), **quiet),
    ]

    scored = score_opportunities(states, _default_policy(), NOW)

    assert [item.opportunity_id for item in scored] == ["q30-a", "q30-b"]
    assert all(item.reasons[0].reason == "high_value_quiet" for item in scored)


def test_high_value_cohort_is_built_before_cooldown_filtering():
    policy = _default_policy()
    high_value = policy.reasons["high_value_quiet"]
    reasons = dict(policy.reasons)
    reasons["high_value_quiet"] = replace(
        high_value, params={**high_value.params, "high_percentile": Decimal(".70")}
    )
    policy = replace(policy, reasons=reasons)
    quiet = {
        "created_at": NOW - timedelta(days=10),
        "last_viewed_at": None,
    }
    states = [
        _state("q10", value=Decimal("10"), **quiet),
        _state("q20-a", value=Decimal("20"), **quiet),
        _state("q20-b", value=Decimal("20"), **quiet),
        _state("q30", value=Decimal("30"), **quiet),
        _state(
            "q100-cooldown",
            value=Decimal("100"),
            contact_key="cooldown-contact",
            last_outbound_at=NOW - timedelta(hours=1),
            **quiet,
        ),
    ]

    assert [
        item.opportunity_id
        for item in score_opportunities(states, policy, NOW)
    ] == ["q30"]


def test_one_primary_reason_keeps_repeat_views_after_contact_reply():
    state = _state(
        "overlap",
        last_outbound_at=NOW - timedelta(days=4),
        last_viewed_at=NOW - timedelta(days=1),
        last_replied_at=NOW - timedelta(hours=12),
        view_timestamps=(NOW - timedelta(days=2), NOW - timedelta(days=1)),
    )

    [scored] = score_opportunities([state], _default_policy(), NOW)

    assert scored.reasons[0].reason == "replied_no_answer"
    assert _reason_names(scored) == ("replied_no_answer", "repeat_views")


def test_repeat_views_count_only_distinct_business_dates_after_outbound():
    outbound = NOW - timedelta(days=4)
    one_after = _state(
        "one-after",
        last_outbound_at=outbound,
        last_viewed_at=NOW - timedelta(days=2),
        view_timestamps=(NOW - timedelta(days=5), NOW - timedelta(days=2)),
    )
    two_after = replace(
        one_after,
        opportunity_id="two-after",
        view_timestamps=(NOW - timedelta(days=3), NOW - timedelta(days=2)),
    )

    scored = {
        item.opportunity_id: item
        for item in score_opportunities(
            [one_after, two_after], _default_policy(), NOW
        )
    }

    assert "repeat_views" not in _reason_names(scored["one-after"])
    assert "repeat_views" in _reason_names(scored["two-after"])


def test_view_reasons_require_a_view_after_latest_contact_outbound():
    state = _state(
        "stale-views",
        last_viewed_at=NOW - timedelta(days=5),
        last_outbound_at=NOW - timedelta(days=4),
    )

    [scored] = score_opportunities([state], _default_policy(), NOW)

    assert "viewed_no_reply" not in _reason_names(scored)
    assert "repeat_views" not in _reason_names(scored)


def test_dead_after_days_uses_configured_business_calendar_dates():
    origin = datetime(2026, 8, 20, 4, 45, tzinfo=UTC)
    now = datetime(2026, 8, 20, 5, 30, tzinfo=UTC)
    aging_only = {
        "aging": ReasonPolicy(
            "opportunity_age",
            Decimal("10"),
            "gentle",
            {"minimum_days": 0, "horizon_days": 1},
        )
    }
    utc_policy = replace(
        _default_policy(),
        business_context=BusinessContext("UTC"),
        dead_after_days=1,
        reasons=aging_only,
    )
    chicago_policy = replace(
        utc_policy, business_context=BusinessContext("America/Chicago")
    )
    state = _state(
        "calendar-boundary",
        created_at=origin,
        last_viewed_at=None,
    )

    assert [
        item.opportunity_id
        for item in score_opportunities([state], utc_policy, now)
    ] == [
        "calendar-boundary"
    ]
    assert score_opportunities([state], chicago_policy, now) == []
