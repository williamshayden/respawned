"""Policy loading and deterministic quote scoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

import yaml

from follow_up_engine.core.context import BusinessContext
from follow_up_engine.core.reduce import QuoteState


KNOWN_REASONS = frozenset(
    {
        "replied_no_answer",
        "viewed_no_reply",
        "high_value_quiet",
        "repeat_views",
        "aging",
    }
)


@dataclass(frozen=True, slots=True)
class ReasonPolicy:
    base: Decimal
    amount_weight: Decimal
    recency_weight: Decimal
    priority: int
    recency_days: Decimal
    min_signal_age_hours: Decimal = Decimal("0")
    quiet_days: int = 0
    min_view_days: int = 0
    aging_days: int = 0
    tone: str = "professional check-in"


@dataclass(frozen=True, slots=True)
class DraftingPolicy:
    sign_off: str = "Service Team"
    max_characters: int = 320
    require_tech_name: bool = False


@dataclass(frozen=True, slots=True)
class Policy:
    business_context: BusinessContext
    cooldown_hours: Decimal
    dead_after_days: int
    high_pct: Decimal
    reasons: Mapping[str, ReasonPolicy]
    drafting: DraftingPolicy = DraftingPolicy()


@dataclass(frozen=True, slots=True)
class ReasonContribution:
    reason: str
    score: Decimal
    base_factor: Decimal
    amount_factor: Decimal
    recency_factor: Decimal
    signal_at: datetime | None
    priority: int


@dataclass(frozen=True, slots=True)
class ScoredQuote:
    quote_id: str
    customer_phone: str | None
    primary_reason: str
    score: Decimal
    base_factor: Decimal
    amount_factor: Decimal
    recency_factor: Decimal
    matched_reasons: tuple[ReasonContribution, ...]
    amount_percentile: Decimal
    high_value_cutoff: Decimal | None
    effective_last_outbound_at: datetime | None


def _decimal(value: object, field: str) -> Decimal:
    try:
        converted = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"Invalid numeric policy field {field!r}") from exc
    if not converted.is_finite():
        raise ValueError(f"Invalid numeric policy field {field!r}")
    return converted


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Invalid integer policy field {field!r}")
    try:
        converted = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer policy field {field!r}") from exc
    if converted < 0 or Decimal(str(value)) != converted:
        raise ValueError(f"Invalid integer policy field {field!r}")
    return converted


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid text policy field {field!r}")
    return value.strip()


def load_policy(path: str | Path) -> Policy:
    """Load and minimally validate a scoring policy YAML file."""
    policy_path = Path(path)
    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Unable to load policy {policy_path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Policy {policy_path} must contain a mapping")

    try:
        business_context = BusinessContext(raw.get("business_timezone", "UTC"))
        cooldown_hours = _decimal(raw["cooldown_hours"], "cooldown_hours")
        dead_after_days = _nonnegative_int(
            raw["dead_after_days"], "dead_after_days"
        )
        high_pct = _decimal(raw["high_pct"], "high_pct")
        raw_reasons = raw["reasons"]
        raw_drafting = raw.get("drafting", {})
    except KeyError as exc:
        raise ValueError(f"Policy {policy_path} is missing {exc.args[0]!r}") from exc

    if cooldown_hours < 0:
        raise ValueError("cooldown_hours must be non-negative")
    if dead_after_days <= 0:
        raise ValueError("dead_after_days must be positive")
    if not Decimal("0") < high_pct <= Decimal("1"):
        raise ValueError("high_pct must be greater than zero and at most one")
    if not isinstance(raw_reasons, dict):
        raise ValueError("reasons must be a mapping")
    if not isinstance(raw_drafting, dict):
        raise ValueError("drafting must be a mapping")

    require_tech_name = raw_drafting.get("require_tech_name", False)
    if not isinstance(require_tech_name, bool):
        raise ValueError(
            "Invalid boolean policy field 'drafting.require_tech_name'"
        )
    drafting = DraftingPolicy(
        sign_off=_nonempty_string(
            raw_drafting.get("sign_off", "Service Team"),
            "drafting.sign_off",
        ),
        max_characters=_nonnegative_int(
            raw_drafting.get("max_characters", 320),
            "drafting.max_characters",
        ),
        require_tech_name=require_tech_name,
    )
    if drafting.max_characters <= 0:
        raise ValueError("drafting.max_characters must be positive")

    unknown = set(raw_reasons) - KNOWN_REASONS
    if unknown:
        raise ValueError(f"Unknown scoring reasons: {sorted(unknown)!r}")

    reasons: dict[str, ReasonPolicy] = {}
    for reason, values in raw_reasons.items():
        if not isinstance(values, dict):
            raise ValueError(f"Reason {reason!r} must be a mapping")
        try:
            parsed = ReasonPolicy(
                base=_decimal(values["base"], f"{reason}.base"),
                amount_weight=_decimal(
                    values["amount_weight"], f"{reason}.amount_weight"
                ),
                recency_weight=_decimal(
                    values["recency_weight"], f"{reason}.recency_weight"
                ),
                priority=_nonnegative_int(
                    values["priority"], f"{reason}.priority"
                ),
                recency_days=_decimal(
                    values.get("recency_days", 7), f"{reason}.recency_days"
                ),
                min_signal_age_hours=_decimal(
                    values.get("min_signal_age_hours", 0),
                    f"{reason}.min_signal_age_hours",
                ),
                quiet_days=_nonnegative_int(
                    values.get("quiet_days", 0), f"{reason}.quiet_days"
                ),
                min_view_days=_nonnegative_int(
                    values.get("min_view_days", 0), f"{reason}.min_view_days"
                ),
                aging_days=_nonnegative_int(
                    values.get("aging_days", 0), f"{reason}.aging_days"
                ),
                tone=_nonempty_string(
                    values.get("tone", "professional check-in"),
                    f"{reason}.tone",
                ),
            )
        except KeyError as exc:
            raise ValueError(
                f"Reason {reason!r} is missing {exc.args[0]!r}"
            ) from exc
        numeric_values = (
            parsed.base,
            parsed.amount_weight,
            parsed.recency_weight,
            parsed.min_signal_age_hours,
        )
        if any(value < 0 for value in numeric_values) or parsed.recency_days <= 0:
            raise ValueError(f"Reason {reason!r} has an invalid negative value")
        reasons[reason] = parsed

    return Policy(
        business_context=business_context,
        cooldown_hours=cooldown_hours,
        dead_after_days=dead_after_days,
        high_pct=high_pct,
        reasons=reasons,
        drafting=drafting,
    )


def _phone_key(phone: str | None) -> str | None:
    if phone is None:
        return None
    stripped = phone.strip()
    return stripped or None


def _open_status(state: QuoteState) -> bool:
    return isinstance(state.status, str) and state.status.strip().lower() == "open"


def _aware_utc(value: datetime, field: str, quote_id: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{quote_id}.{field} must be timezone-aware")
    return value.astimezone(UTC)


def _origin(state: QuoteState) -> datetime | None:
    return state.quote_sent_at or state.created_at


def _calendar_days_since(
    value: datetime,
    now: datetime,
    timezone: ZoneInfo,
) -> int:
    local_value = value.astimezone(timezone).date()
    local_now = now.astimezone(timezone).date()
    return max(0, (local_now - local_value).days)


def _elapsed_hours(value: datetime, now: datetime) -> Decimal:
    seconds = Decimal(str((now - value).total_seconds()))
    return max(Decimal("0"), seconds / Decimal("3600"))


def _positive_amount(state: QuoteState) -> Decimal | None:
    amount = state.amount
    if amount is None:
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    return amount


def _is_dead(
    state: QuoteState,
    policy: Policy,
    now: datetime,
    timezone: ZoneInfo,
) -> bool:
    origin = _origin(state)
    if origin is not None:
        origin = _aware_utc(origin, "quote_origin_at", state.quote_id)
    return bool(
        origin is not None
        and _calendar_days_since(origin, now, timezone) >= policy.dead_after_days
    )


def _latest_outbounds(
    states: Sequence[QuoteState],
) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    for state in states:
        phone = _phone_key(state.customer_phone)
        outbound = state.last_outbound_at
        if phone is None or outbound is None:
            continue
        outbound = _aware_utc(outbound, "last_outbound_at", state.quote_id)
        if phone not in latest or outbound > latest[phone]:
            latest[phone] = outbound
    return latest


def _high_value_cutoff(
    states: Sequence[QuoteState],
    policy: Policy,
    now: datetime,
    timezone: ZoneInfo,
) -> tuple[tuple[Decimal, ...], Decimal | None]:
    amounts = tuple(
        sorted(
            amount
            for state in states
            if _open_status(state) and not _is_dead(state, policy, now, timezone)
            if (amount := _positive_amount(state)) is not None
        )
    )
    if not amounts:
        return amounts, None
    rank = int(
        (policy.high_pct * Decimal(len(amounts))).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    return amounts, amounts[rank - 1]


def _amount_percentile(amount: Decimal | None, population: Sequence[Decimal]) -> Decimal:
    if amount is None or not population:
        return Decimal("0")
    less = sum(candidate < amount for candidate in population)
    equal = sum(candidate == amount for candidate in population)
    return (Decimal(less) + Decimal(equal) / Decimal("2")) / Decimal(
        len(population)
    )


def _freshness(policy: ReasonPolicy, signal_at: datetime, now: datetime) -> Decimal:
    horizon_hours = policy.recency_days * Decimal("24")
    return max(
        Decimal("0"),
        Decimal("1") - _elapsed_hours(signal_at, now) / horizon_hours,
    )


def _aging_factor(age_days: int, threshold: int, policy: ReasonPolicy) -> Decimal:
    return min(
        Decimal("1"),
        max(
            Decimal("0"),
            Decimal(age_days - threshold) / policy.recency_days,
        ),
    )


def _view_days_after_outbound(
    state: QuoteState,
    outbound: datetime | None,
    timezone: ZoneInfo,
) -> int:
    business_dates = set()
    for value in state.view_timestamps:
        viewed_at = _aware_utc(value, "view_timestamps", state.quote_id)
        if outbound is None or viewed_at > outbound:
            business_dates.add(viewed_at.astimezone(timezone).date())
    return len(business_dates)


def _contribution(
    reason: str,
    policy: ReasonPolicy,
    amount_percentile: Decimal,
    recency_multiplier: Decimal,
    signal_at: datetime | None,
) -> ReasonContribution:
    amount_factor = policy.amount_weight * amount_percentile
    recency_factor = policy.recency_weight * recency_multiplier
    return ReasonContribution(
        reason=reason,
        score=policy.base + amount_factor + recency_factor,
        base_factor=policy.base,
        amount_factor=amount_factor,
        recency_factor=recency_factor,
        signal_at=signal_at,
        priority=policy.priority,
    )


def score_quotes(
    states: Sequence[QuoteState],
    policy: Policy,
    now: datetime,
) -> list[ScoredQuote]:
    """Score eligible quotes from one complete reduced-state snapshot."""
    now = _aware_utc(now, "now", "score_quotes")
    timezone = ZoneInfo(policy.business_context.timezone_name)
    customer_outbounds = _latest_outbounds(states)
    amount_population, high_value_cutoff = _high_value_cutoff(
        states, policy, now, timezone
    )
    results: list[ScoredQuote] = []

    for state in states:
        if not _open_status(state) or _is_dead(state, policy, now, timezone):
            continue
        if state.view_days < 0:
            raise ValueError(f"{state.quote_id}.view_days must be non-negative")

        phone = _phone_key(state.customer_phone)
        effective_outbound = customer_outbounds.get(phone) if phone else None
        if effective_outbound is None and state.last_outbound_at is not None:
            effective_outbound = _aware_utc(
                state.last_outbound_at, "last_outbound_at", state.quote_id
            )
        if effective_outbound is not None:
            outbound_age = _elapsed_hours(effective_outbound, now)
            if outbound_age < policy.cooldown_hours:
                continue

        timestamps: dict[str, datetime | None] = {}
        for field in ("last_viewed_at", "last_replied_at"):
            value = getattr(state, field)
            timestamps[field] = (
                _aware_utc(value, field, state.quote_id) if value is not None else None
            )
        viewed_at = timestamps["last_viewed_at"]
        replied_at = timestamps["last_replied_at"]
        origin = _origin(state)
        if origin is not None:
            origin = _aware_utc(origin, "quote_origin_at", state.quote_id)

        amount = _positive_amount(state)
        percentile = _amount_percentile(amount, amount_population)
        matches: list[ReasonContribution] = []

        reason_policy = policy.reasons.get("replied_no_answer")
        if (
            reason_policy is not None
            and replied_at is not None
            and (effective_outbound is None or replied_at > effective_outbound)
        ):
            matches.append(
                _contribution(
                    "replied_no_answer",
                    reason_policy,
                    percentile,
                    _freshness(reason_policy, replied_at, now),
                    replied_at,
                )
            )

        view_is_current = bool(
            viewed_at is not None
            and (effective_outbound is None or viewed_at > effective_outbound)
        )
        reason_policy = policy.reasons.get("viewed_no_reply")
        if (
            reason_policy is not None
            and view_is_current
            and (replied_at is None or replied_at < viewed_at)
            and _elapsed_hours(viewed_at, now) >= reason_policy.min_signal_age_hours
        ):
            matches.append(
                _contribution(
                    "viewed_no_reply",
                    reason_policy,
                    percentile,
                    _freshness(reason_policy, viewed_at, now),
                    viewed_at,
                )
            )

        reason_policy = policy.reasons.get("repeat_views")
        if (
            reason_policy is not None
            and view_is_current
            and _view_days_after_outbound(state, effective_outbound, timezone)
            >= reason_policy.min_view_days
        ):
            matches.append(
                _contribution(
                    "repeat_views",
                    reason_policy,
                    percentile,
                    _freshness(reason_policy, viewed_at, now),
                    viewed_at,
                )
            )

        reason_policy = policy.reasons.get("high_value_quiet")
        if (
            reason_policy is not None
            and amount is not None
            and high_value_cutoff is not None
            and amount >= high_value_cutoff
            and origin is not None
        ):
            contact_times = tuple(
                value
                for value in (origin, effective_outbound, replied_at)
                if value is not None
            )
            quiet_since = max(contact_times)
            quiet_age_days = _calendar_days_since(quiet_since, now, timezone)
            if quiet_age_days >= reason_policy.quiet_days:
                matches.append(
                    _contribution(
                        "high_value_quiet",
                        reason_policy,
                        percentile,
                        _aging_factor(
                            quiet_age_days,
                            reason_policy.quiet_days,
                            reason_policy,
                        ),
                        quiet_since,
                    )
                )

        reason_policy = policy.reasons.get("aging")
        if reason_policy is not None and origin is not None:
            quote_age_days = _calendar_days_since(origin, now, timezone)
            if quote_age_days >= reason_policy.aging_days:
                matches.append(
                    _contribution(
                        "aging",
                        reason_policy,
                        percentile,
                        _aging_factor(
                            quote_age_days,
                            reason_policy.aging_days,
                            reason_policy,
                        ),
                        origin,
                    )
                )

        if not matches:
            continue
        matches.sort(key=lambda item: (-item.score, item.priority, item.reason))
        primary = matches[0]
        results.append(
            ScoredQuote(
                quote_id=state.quote_id,
                customer_phone=phone,
                primary_reason=primary.reason,
                score=primary.score,
                base_factor=primary.base_factor,
                amount_factor=primary.amount_factor,
                recency_factor=primary.recency_factor,
                matched_reasons=tuple(matches),
                amount_percentile=percentile,
                high_value_cutoff=high_value_cutoff,
                effective_last_outbound_at=effective_outbound,
            )
        )

    return sorted(results, key=lambda item: item.quote_id)
