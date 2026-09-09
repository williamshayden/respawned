"""Deterministic opportunity ranking over configurable signal evaluators."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from respawned.core.contact import normalize_contact_key
from respawned.core.domain import OpportunityState
from respawned.core.opportunity import (
    is_contactable_opportunity,
    positive_value,
)
from respawned.core.policy import Policy
from respawned.core.reasons import (
    EVALUATORS,
    ReasonContext,
    ReasonEvaluator,
    ReasonFactory,
    ReasonMatch,
)
from respawned.core.time import aware_utc, within_cooldown


@dataclass(frozen=True, slots=True)
class ScoredReason:
    reason: str
    signal_strength: Decimal
    signal_at: datetime | None
    score: Decimal


@dataclass(frozen=True, slots=True)
class ScoredOpportunity:
    opportunity_id: str
    score: Decimal
    value_percentile: Decimal
    reasons: tuple[ScoredReason, ...]


def _latest_outbounds(
    states: Sequence[OpportunityState], now: datetime
) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for state in states:
        key = normalize_contact_key(state.contact_key)
        if key is None or state.last_outbound_at is None:
            continue
        outbound = aware_utc(
            state.last_outbound_at, f"{state.opportunity_id}.last_outbound_at"
        )
        if outbound <= now:
            latest[key] = max(outbound, latest.get(key, outbound))
    return latest


def _value_percentiles(population: Sequence[Decimal]) -> dict[Decimal, Decimal]:
    """Return midpoint percentiles in O(n log n), including tied values."""

    counts = Counter(population)
    total = Decimal(len(population))
    lower = 0
    result: dict[Decimal, Decimal] = {}
    for value in sorted(counts):
        equal = counts[value]
        result[value] = (Decimal(lower) + Decimal(equal) / 2) / total
        lower += equal
    return result


def _configured_evaluators(
    policy: Policy,
    factories: Mapping[str, ReasonFactory] | None,
) -> tuple[tuple[str, Decimal, ReasonEvaluator], ...]:
    available = dict(EVALUATORS)
    available.update(factories or {})
    missing = sorted(
        {
            reason.evaluator
            for reason in policy.reasons.values()
            if reason.evaluator not in available
        }
    )
    if missing:
        raise ValueError(f"Unknown reason evaluators: {missing!r}")
    configured = []
    for name, reason in policy.reasons.items():
        try:
            evaluator = available[reason.evaluator](reason.params)
        except ValueError as exc:
            raise ValueError(f"Invalid configuration for reason {name!r}: {exc}") from exc
        configured.append((name, reason.base_score, evaluator))
    return tuple(configured)


def score_opportunities(
    states: Sequence[OpportunityState],
    policy: Policy,
    now: datetime,
    *,
    evaluators: Mapping[str, ReasonFactory] | None = None,
) -> list[ScoredOpportunity]:
    """Rank eligible opportunities; custom factories may extend built-ins."""

    now = aware_utc(now, "score_opportunities.now")
    timezone = ZoneInfo(policy.business_context.timezone_name)
    configured = _configured_evaluators(policy, evaluators)
    outbounds = _latest_outbounds(states, now)
    eligible = tuple(
        state
        for state in states
        if is_contactable_opportunity(state, now, timezone, policy.dead_after_days)
    )
    population = tuple(
        sorted(
            value
            for state in eligible
            if (value := positive_value(state)) is not None
        )
    )
    percentiles = _value_percentiles(population) if population else {}
    scored: list[ScoredOpportunity] = []

    for state in eligible:
        key = normalize_contact_key(state.contact_key)
        outbound = outbounds.get(key) if key is not None else None
        if outbound is None and state.last_outbound_at is not None:
            direct = aware_utc(
                state.last_outbound_at, f"{state.opportunity_id}.last_outbound_at"
            )
            outbound = direct if direct <= now else None
        if outbound is not None and within_cooldown(
            outbound, now, policy.cooldown_hours
        ):
            continue

        context = ReasonContext(state, now, timezone, outbound, population)
        matches: list[ScoredReason] = []
        for reason, base_score, evaluator in configured:
            match = evaluator(context)
            if match is None:
                continue
            if not isinstance(match, ReasonMatch):
                raise TypeError(f"Reason evaluator {reason!r} returned an invalid match")
            if match.signal_at is not None and aware_utc(
                match.signal_at, f"{reason}.signal_at"
            ) > now:
                raise ValueError(f"Reason evaluator {reason!r} returned a future signal")
            matches.append(
                ScoredReason(
                    reason=reason,
                    signal_strength=match.signal_strength,
                    signal_at=match.signal_at,
                    score=base_score
                    + policy.ranking.signal_weight * match.signal_strength,
                )
            )
        if not matches:
            continue
        matches.sort(key=lambda item: (-item.score, item.reason))
        value = positive_value(state)
        percentile = percentiles.get(value, Decimal("0"))
        scored.append(
            ScoredOpportunity(
                opportunity_id=state.opportunity_id,
                score=matches[0].score
                + policy.ranking.value_weight * percentile,
                value_percentile=percentile,
                reasons=tuple(matches),
            )
        )

    return sorted(scored, key=lambda item: item.opportunity_id)
