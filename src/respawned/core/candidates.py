"""Customer-level candidate selection and final cooldown enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from respawned.core.reduce import QuoteState
from respawned.core.score import ScoredQuote


@dataclass(frozen=True, slots=True)
class Candidate:
    id: UUID
    run_at: datetime
    primary_quote_id: str
    customer_phone: str
    reason: str
    score: Decimal
    other_quote_ids: tuple[str, ...]
    channel: str = "sms"
    customer_name: str = ""


def _phone_key(phone: str | None) -> str | None:
    if phone is None:
        return None
    stripped = phone.strip()
    return stripped or None


def _is_open(state: QuoteState) -> bool:
    return isinstance(state.status, str) and state.status.strip().lower() == "open"


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _latest_outbound(states: Sequence[QuoteState]) -> datetime | None:
    values = (
        _aware_utc(state.last_outbound_at, f"{state.quote_id}.last_outbound_at")
        for state in states
        if state.last_outbound_at is not None
    )
    return max(values, default=None)


def _signal_at(scored: ScoredQuote) -> datetime | None:
    for contribution in scored.matched_reasons:
        if contribution.reason == scored.primary_reason:
            return contribution.signal_at
    return None


def _candidate_id(
    phone: str,
    scored: ScoredQuote,
    open_quote_ids: Sequence[str],
    latest_outbound_at: datetime | None,
) -> UUID:
    signal_at = _signal_at(scored)
    identity = {
        "customer_phone": phone,
        "latest_outbound_at": (
            _aware_utc(latest_outbound_at, "latest_outbound_at").isoformat()
            if latest_outbound_at is not None
            else None
        ),
        "open_quote_ids": list(open_quote_ids),
        "primary_quote_id": scored.quote_id,
        "reason": scored.primary_reason,
        "signal_at": (
            _aware_utc(signal_at, "signal_at").isoformat()
            if signal_at is not None
            else None
        ),
        "version": 1,
    }
    # Persisted UUID namespace: preserve existing candidate and draft identities.
    # This legacy protocol constant is independent of the Respawned product name.
    return uuid5(
        NAMESPACE_URL,
        "follow-up-engine:candidate:"
        + json.dumps(identity, sort_keys=True, separators=(",", ":")),
    )


def select_candidates(
    states: Sequence[QuoteState],
    scored: Sequence[ScoredQuote],
    now: datetime,
    cooldown_hours: Decimal,
) -> list[Candidate]:
    """Collapse scored quotes to one cooldown-safe candidate per customer."""
    run_at = _aware_utc(now, "now")
    cooldown = Decimal(str(cooldown_hours))
    if not cooldown.is_finite() or cooldown < 0:
        raise ValueError("cooldown_hours must be a non-negative finite number")

    states_by_phone: dict[str, list[QuoteState]] = {}
    state_by_id: dict[str, QuoteState] = {}
    for state in states:
        state_by_id[state.quote_id] = state
        phone = _phone_key(state.customer_phone)
        if phone is not None:
            states_by_phone.setdefault(phone, []).append(state)

    scored_by_phone: dict[str, list[ScoredQuote]] = {}
    for item in scored:
        state = state_by_id.get(item.quote_id)
        if state is None or not _is_open(state):
            continue
        phone = _phone_key(state.customer_phone)
        if phone is not None:
            scored_by_phone.setdefault(phone, []).append(item)

    candidates: list[Candidate] = []
    for phone, customer_scores in scored_by_phone.items():
        siblings = states_by_phone[phone]
        latest_outbound_at = _latest_outbound(siblings)
        if latest_outbound_at is not None:
            elapsed = Decimal(str((run_at - latest_outbound_at).total_seconds()))
            if elapsed / Decimal("3600") < cooldown:
                continue

        customer_scores.sort(key=lambda item: (-item.score, item.quote_id))
        primary = customer_scores[0]
        primary_state = state_by_id[primary.quote_id]
        open_quote_ids = tuple(
            sorted({state.quote_id for state in siblings if _is_open(state)})
        )
        other_quote_ids = tuple(
            quote_id for quote_id in open_quote_ids if quote_id != primary.quote_id
        )
        candidates.append(
            Candidate(
                id=_candidate_id(
                    phone,
                    primary,
                    open_quote_ids,
                    latest_outbound_at,
                ),
                run_at=run_at,
                primary_quote_id=primary.quote_id,
                customer_phone=phone,
                customer_name=primary_state.customer_name,
                reason=primary.primary_reason,
                score=primary.score,
                other_quote_ids=other_quote_ids,
                channel=(
                    primary_state.channel
                    if primary_state.channel in {"email", "sms"}
                    else "sms"
                ),
            )
        )

    return sorted(
        candidates,
        key=lambda item: (-item.score, item.primary_quote_id, item.customer_phone),
    )
