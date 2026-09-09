"""Configurable signal evaluators for deterministic ranking."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from types import MappingProxyType
from zoneinfo import ZoneInfo

from respawned.core.configuration import integer, number, strict_mapping
from respawned.core.domain import OpportunityState
from respawned.core.opportunity import opportunity_origin, positive_value
from respawned.core.time import aware_utc, elapsed_hours, local_calendar_days_since


@dataclass(frozen=True, slots=True)
class ReasonContext:
    state: OpportunityState
    now: datetime
    timezone: ZoneInfo
    effective_last_outbound_at: datetime | None = None
    value_population: tuple[Decimal, ...] = ()


@dataclass(frozen=True, slots=True)
class ReasonMatch:
    signal_strength: Decimal
    signal_at: datetime | None

    def __post_init__(self) -> None:
        strength = self.signal_strength
        if (
            not isinstance(strength, Decimal)
            or not strength.is_finite()
            or not Decimal("0") <= strength <= Decimal("1")
        ):
            raise ValueError("signal_strength must be between 0 and 1")
        if self.signal_at is not None:
            aware_utc(self.signal_at, "signal_at")


ReasonEvaluator = Callable[[ReasonContext], ReasonMatch | None]
ReasonFactory = Callable[[Mapping[str, object]], ReasonEvaluator]


def _freshness(signal_at: datetime, context: ReasonContext, horizon: Decimal) -> Decimal:
    return max(
        Decimal("0"),
        Decimal("1") - elapsed_hours(signal_at, context.now) / (horizon * 24),
    )


def _maturity(age: int, minimum: int, horizon: Decimal) -> Decimal:
    return min(Decimal("1"), max(Decimal("0"), Decimal(age - minimum) / horizon))


def reply_after_outbound(params: Mapping[str, object]) -> ReasonEvaluator:
    name = "reply_after_outbound"
    values = strict_mapping(params, name, {"horizon_days"})
    horizon = number(values["horizon_days"], f"{name}.horizon_days", positive=True)

    def evaluate(context: ReasonContext) -> ReasonMatch | None:
        reply = context.state.last_replied_at
        if reply is None:
            return None
        reply = aware_utc(reply, f"{context.state.opportunity_id}.last_replied_at")
        outbound = context.effective_last_outbound_at
        if reply > context.now or (outbound is not None and reply <= outbound):
            return None
        return ReasonMatch(_freshness(reply, context, horizon), reply)

    return evaluate


def view_after_outbound(params: Mapping[str, object]) -> ReasonEvaluator:
    name = "view_after_outbound"
    values = strict_mapping(params, name, {"horizon_days", "minimum_age_hours"})
    horizon = number(values["horizon_days"], f"{name}.horizon_days", positive=True)
    minimum_age = number(values["minimum_age_hours"], f"{name}.minimum_age_hours")

    def evaluate(context: ReasonContext) -> ReasonMatch | None:
        viewed = context.state.last_viewed_at
        if viewed is None:
            return None
        viewed = aware_utc(viewed, f"{context.state.opportunity_id}.last_viewed_at")
        outbound = context.effective_last_outbound_at
        if viewed > context.now or (outbound is not None and viewed <= outbound):
            return None
        replied = context.state.last_replied_at
        if replied is not None:
            replied = aware_utc(
                replied, f"{context.state.opportunity_id}.last_replied_at"
            )
            if replied <= context.now and replied >= viewed:
                return None
        if elapsed_hours(viewed, context.now) < minimum_age:
            return None
        return ReasonMatch(_freshness(viewed, context, horizon), viewed)

    return evaluate


def distinct_view_days(params: Mapping[str, object]) -> ReasonEvaluator:
    name = "distinct_view_days"
    values = strict_mapping(
        params, name, {"minimum_days", "saturation_days", "horizon_days"}
    )
    minimum = integer(values["minimum_days"], f"{name}.minimum_days", positive=True)
    saturation = integer(
        values["saturation_days"], f"{name}.saturation_days", positive=True
    )
    horizon = number(values["horizon_days"], f"{name}.horizon_days", positive=True)
    if saturation < minimum:
        raise ValueError(f"{name}.saturation_days must be at least minimum_days")

    def evaluate(context: ReasonContext) -> ReasonMatch | None:
        outbound = context.effective_last_outbound_at
        views = tuple(
            aware_utc(value, f"{context.state.opportunity_id}.view_timestamps")
            for value in context.state.view_timestamps
        )
        qualifying = tuple(
            value
            for value in views
            if value <= context.now and (outbound is None or value > outbound)
        )
        dates = {value.astimezone(context.timezone).date() for value in qualifying}
        if len(dates) < minimum:
            return None
        latest = max(qualifying)
        intensity = min(Decimal("1"), Decimal(len(dates)) / Decimal(saturation))
        return ReasonMatch(intensity * _freshness(latest, context, horizon), latest)

    return evaluate


def high_value_quiet(params: Mapping[str, object]) -> ReasonEvaluator:
    name = "high_value_quiet"
    values = strict_mapping(
        params, name, {"high_percentile", "quiet_days", "horizon_days"}
    )
    percentile = number(
        values["high_percentile"],
        f"{name}.high_percentile",
        positive=True,
        maximum=Decimal("1"),
    )
    minimum = integer(values["quiet_days"], f"{name}.quiet_days")
    horizon = number(values["horizon_days"], f"{name}.horizon_days", positive=True)

    def evaluate(context: ReasonContext) -> ReasonMatch | None:
        value = positive_value(context.state)
        population = context.value_population
        if value is None or not population:
            return None
        rank = int(
            (percentile * len(population)).to_integral_value(rounding=ROUND_CEILING)
        )
        if value < population[rank - 1]:
            return None
        origin = opportunity_origin(context.state)
        if origin is None:
            return None
        contacts = [aware_utc(origin, f"{context.state.opportunity_id}.created_at")]
        if context.effective_last_outbound_at is not None:
            contacts.append(context.effective_last_outbound_at)
        if context.state.last_replied_at is not None:
            contacts.append(
                aware_utc(
                    context.state.last_replied_at,
                    f"{context.state.opportunity_id}.last_replied_at",
                )
            )
        eligible_contacts = [value for value in contacts if value <= context.now]
        if not eligible_contacts:
            return None
        quiet_since = max(eligible_contacts)
        age = local_calendar_days_since(quiet_since, context.now, context.timezone)
        if age < minimum:
            return None
        return ReasonMatch(_maturity(age, minimum, horizon), quiet_since)

    return evaluate


def opportunity_age(params: Mapping[str, object]) -> ReasonEvaluator:
    name = "opportunity_age"
    values = strict_mapping(params, name, {"minimum_days", "horizon_days"})
    minimum = integer(values["minimum_days"], f"{name}.minimum_days")
    horizon = number(values["horizon_days"], f"{name}.horizon_days", positive=True)

    def evaluate(context: ReasonContext) -> ReasonMatch | None:
        origin = opportunity_origin(context.state)
        if origin is None:
            return None
        origin = aware_utc(origin, f"{context.state.opportunity_id}.created_at")
        if origin > context.now:
            return None
        age = local_calendar_days_since(origin, context.now, context.timezone)
        if age < minimum:
            return None
        return ReasonMatch(_maturity(age, minimum, horizon), origin)

    return evaluate


EVALUATORS: Mapping[str, ReasonFactory] = MappingProxyType(
    {
        "reply_after_outbound": reply_after_outbound,
        "view_after_outbound": view_after_outbound,
        "distinct_view_days": distinct_view_days,
        "high_value_quiet": high_value_quiet,
        "opportunity_age": opportunity_age,
    }
)
