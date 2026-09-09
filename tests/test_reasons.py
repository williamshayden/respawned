from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from respawned.core.domain import OpportunityState
from respawned.core.reasons import EVALUATORS, ReasonContext, ReasonMatch


NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
UTC_ZONE = ZoneInfo("UTC")
UNSET = object()
VALID_PARAMS = {
    "reply_after_outbound": {"horizon_days": 7},
    "view_after_outbound": {"horizon_days": 7, "minimum_age_hours": 24},
    "distinct_view_days": {
        "minimum_days": 2,
        "saturation_days": 4,
        "horizon_days": 7,
    },
    "high_value_quiet": {
        "high_percentile": Decimal("0.75"),
        "quiet_days": 3,
        "horizon_days": 14,
    },
    "opportunity_age": {"minimum_days": 14, "horizon_days": 14},
    "awaiting_reply": {"minimum_days": 7, "horizon_days": 14},
    "application_no_update": {"minimum_days": 7, "horizon_days": 14},
    "promised_update_overdue": {"grace_days": 1, "horizon_days": 7},
}


def _context(
    *,
    now: datetime = NOW,
    timezone: ZoneInfo = UTC_ZONE,
    effective_last_outbound_at: datetime | None | object = UNSET,
    value_population: tuple[Decimal, ...] = (
        Decimal("10"),
        Decimal("20"),
        Decimal("30"),
        Decimal("100"),
    ),
    **state_changes: object,
) -> ReasonContext:
    state = OpportunityState(
        opportunity_id="opportunity-1",
        status="open",
        value=Decimal("30"),
        contact_key="contact-1",
        contact_name="Contact",
        contact_phone="+13125550100",
        owner_name="Sam",
        created_at=NOW - timedelta(days=20),
        last_viewed_at=None,
        last_replied_at=None,
        last_outbound_at=NOW - timedelta(days=4),
    )
    state = replace(state, **state_changes)
    outbound = (
        state.last_outbound_at
        if effective_last_outbound_at is UNSET
        else effective_last_outbound_at
    )
    return ReasonContext(
        state=state,
        now=now,
        timezone=timezone,
        effective_last_outbound_at=outbound,
        value_population=value_population,
    )


def _evaluate(
    name: str, context: ReasonContext, **param_changes: object
) -> ReasonMatch | None:
    params = {**VALID_PARAMS[name], **param_changes}
    return EVALUATORS[name](params)(context)


def test_registry_is_immutable_and_contains_only_builtin_factories():
    assert isinstance(EVALUATORS, MappingProxyType)
    assert set(EVALUATORS) == set(VALID_PARAMS)


def test_reply_after_outbound_triggers_and_decays_from_the_reply():
    replied_at = NOW - timedelta(days=1)
    match = _evaluate(
        "reply_after_outbound", _context(last_replied_at=replied_at)
    )

    assert match == ReasonMatch(Decimal("6") / Decimal("7"), replied_at)
    assert (
        _evaluate(
            "reply_after_outbound",
            _context(last_replied_at=NOW - timedelta(days=4)),
        )
        is None
    )


def test_freshness_reaching_zero_still_returns_a_match():
    replied_at = NOW - timedelta(days=7)
    match = _evaluate(
        "reply_after_outbound",
        _context(
            last_replied_at=replied_at,
            last_outbound_at=None,
            effective_last_outbound_at=None,
        ),
    )

    assert match == ReasonMatch(Decimal("0"), replied_at)


def test_view_after_outbound_observes_age_reply_and_outbound_boundaries():
    viewed_at = NOW - timedelta(hours=24)
    context = _context(
        last_viewed_at=viewed_at,
        last_replied_at=NOW - timedelta(days=2),
    )

    assert _evaluate("view_after_outbound", context) == ReasonMatch(
        Decimal("6") / Decimal("7"), viewed_at
    )
    assert (
        _evaluate(
            "view_after_outbound",
            _context(last_viewed_at=NOW - timedelta(hours=23)),
        )
        is None
    )
    assert (
        _evaluate(
            "view_after_outbound",
            replace(context, state=replace(context.state, last_replied_at=viewed_at)),
        )
        is None
    )
    assert (
        _evaluate(
            "view_after_outbound",
            _context(last_viewed_at=NOW - timedelta(days=4)),
        )
        is None
    )


def test_future_reply_view_and_repeat_events_are_excluded():
    future = NOW + timedelta(days=1)

    assert (
        _evaluate("reply_after_outbound", _context(last_replied_at=future)) is None
    )
    assert (
        _evaluate(
            "view_after_outbound",
            _context(last_viewed_at=future),
            minimum_age_hours=0,
        )
        is None
    )
    assert (
        _evaluate(
            "distinct_view_days",
            _context(view_timestamps=(NOW - timedelta(days=1), future)),
        )
        is None
    )


def test_distinct_view_strength_is_intensity_times_freshness():
    outbound = NOW - timedelta(days=6)
    views = (
        NOW - timedelta(days=7),  # before outbound
        NOW - timedelta(days=5),
        NOW - timedelta(days=3),
        NOW - timedelta(days=3) + timedelta(hours=1),  # same local date
        NOW - timedelta(days=1),
    )
    match = _evaluate(
        "distinct_view_days",
        _context(
            view_timestamps=views,
            effective_last_outbound_at=outbound,
        ),
        horizon_days=8,
    )

    assert match == ReasonMatch(Decimal("21") / Decimal("32"), views[-1])
    assert (
        _evaluate(
            "distinct_view_days",
            _context(
                view_timestamps=(
                    NOW - timedelta(days=1),
                    NOW - timedelta(days=1) + timedelta(hours=1),
                )
            ),
        )
        is None
    )


def test_distinct_view_intensity_saturates():
    views = tuple(NOW - timedelta(days=days) for days in range(5, 0, -1))
    match = _evaluate(
        "distinct_view_days",
        _context(
            view_timestamps=views,
            effective_last_outbound_at=NOW - timedelta(days=6),
        ),
        horizon_days=8,
    )

    assert match == ReasonMatch(Decimal("7") / Decimal("8"), views[-1])


def test_distinct_views_use_configured_local_calendar_dates():
    first = datetime(2026, 8, 20, 23, 30, tzinfo=UTC)
    second = datetime(2026, 8, 21, 0, 30, tzinfo=UTC)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    views = (first, second)

    utc_match = _evaluate(
        "distinct_view_days",
        _context(now=now, timezone=UTC_ZONE, view_timestamps=views),
    )
    chicago_match = _evaluate(
        "distinct_view_days",
        _context(
            now=now,
            timezone=ZoneInfo("America/Chicago"),
            view_timestamps=views,
        ),
    )

    assert utc_match is not None
    assert chicago_match is None


def test_high_value_quiet_uses_nearest_rank_cutoff_including_ties():
    population = (
        Decimal("10"),
        Decimal("20"),
        Decimal("30"),
        Decimal("30"),
    )
    quiet_since = NOW - timedelta(days=5)
    context = _context(
        value=Decimal("30"),
        last_replied_at=quiet_since,
        effective_last_outbound_at=NOW - timedelta(days=6),
        value_population=population,
    )

    match = _evaluate("high_value_quiet", context, horizon_days=4)

    assert match == ReasonMatch(Decimal("0.5"), quiet_since)
    assert (
        _evaluate(
            "high_value_quiet",
            replace(context, state=replace(context.state, value=Decimal("29.99"))),
            horizon_days=4,
        )
        is None
    )
    assert (
        _evaluate(
            "high_value_quiet",
            replace(context, effective_last_outbound_at=NOW - timedelta(days=2)),
            horizon_days=4,
        )
        is None
    )


def test_high_value_quiet_ignores_future_contact_when_measuring_quiet():
    origin = NOW - timedelta(days=5)
    context = _context(
        created_at=origin,
        last_outbound_at=None,
        last_replied_at=NOW + timedelta(days=1),
        effective_last_outbound_at=None,
        value_population=(Decimal("10"), Decimal("20"), Decimal("30")),
    )

    assert _evaluate("high_value_quiet", context, horizon_days=4) == ReasonMatch(
        Decimal("0.5"), origin
    )


def test_opportunity_age_triggers_after_minimum_and_saturates():
    origin = NOW - timedelta(days=20)
    match = _evaluate(
        "opportunity_age",
        _context(created_at=origin),
        horizon_days=12,
    )

    assert match == ReasonMatch(Decimal("0.5"), origin)
    assert (
        _evaluate(
            "opportunity_age",
            _context(
                created_at=NOW - timedelta(days=13),
            ),
        )
        is None
    )
    assert _evaluate(
        "opportunity_age",
        _context(
            created_at=NOW - timedelta(days=40),
        ),
        horizon_days=12,
    ).signal_strength == Decimal("1")


@pytest.mark.parametrize(
    "name, missing",
    [
        (name, parameter)
        for name, params in VALID_PARAMS.items()
        for parameter in params
    ],
)
def test_factories_require_all_parameters(name, missing):
    params = dict(VALID_PARAMS[name])
    params.pop(missing)
    with pytest.raises(ValueError, match=rf"{name} is missing"):
        EVALUATORS[name](params)


@pytest.mark.parametrize("name", VALID_PARAMS)
def test_factories_reject_unknown_parameters(name):
    with pytest.raises(ValueError, match=rf"{name} has unknown fields"):
        EVALUATORS[name]({**VALID_PARAMS[name], "extra": 1})


@pytest.mark.parametrize(
    "name, params, message",
    [
        ("reply_after_outbound", [], "must be a mapping"),
        ("reply_after_outbound", {"horizon_days": "soon"}, "finite number"),
        ("reply_after_outbound", {"horizon_days": True}, "finite number"),
        ("reply_after_outbound", {"horizon_days": 0}, "must be positive"),
        (
            "view_after_outbound",
            {"horizon_days": 7, "minimum_age_hours": -1},
            "must be non-negative",
        ),
        (
            "distinct_view_days",
            {"minimum_days": 1.5, "saturation_days": 4, "horizon_days": 7},
            "must be an integer",
        ),
        (
            "distinct_view_days",
            {"minimum_days": 2, "saturation_days": 1, "horizon_days": 7},
            "must be at least minimum_days",
        ),
        (
            "high_value_quiet",
            {"high_percentile": 0, "quiet_days": 3, "horizon_days": 14},
            "must be positive",
        ),
        (
            "high_value_quiet",
            {"high_percentile": 1.1, "quiet_days": 3, "horizon_days": 14},
            "must be at most 1",
        ),
        (
            "opportunity_age",
            {"minimum_days": -1, "horizon_days": 14},
            "must be non-negative",
        ),
    ],
)
def test_factories_reject_malformed_parameters(name, params, message):
    with pytest.raises(ValueError, match=message):
        EVALUATORS[name](params)


@pytest.mark.parametrize(
    "strength",
    [
        Decimal("-0.01"),
        Decimal("1.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_reason_match_rejects_invalid_strength(strength):
    with pytest.raises(ValueError, match="signal_strength must be between 0 and 1"):
        ReasonMatch(strength, NOW)
