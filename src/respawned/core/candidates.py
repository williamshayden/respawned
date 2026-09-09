"""Contact-level candidate selection and final cooldown enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from respawned.core.contact import (
    normalize_contact_address,
    normalize_contact_key,
)
from respawned.core.domain import ContactPoint, OpportunityState, resolve_contact
from respawned.core.opportunity import is_contactable_opportunity
from respawned.core.policy import Policy
from respawned.core.score import ScoredOpportunity
from respawned.core.time import aware_utc, within_cooldown


@dataclass(frozen=True, slots=True)
class Candidate:
    id: UUID
    run_at: datetime
    primary_opportunity_id: str
    contact_key: str
    reason: str
    score: Decimal
    other_opportunity_ids: tuple[str, ...]
    channel: str
    contact_address: str
    contact_name: str | None = None


def _latest_outbound(
    states: Sequence[OpportunityState], now: datetime
) -> datetime | None:
    values = []
    for state in states:
        if state.last_outbound_at is None:
            continue
        value = aware_utc(
            state.last_outbound_at, f"{state.opportunity_id}.last_outbound_at"
        )
        if value <= now:
            values.append(value)
    return max(values, default=None)


def _route(
    primary: OpportunityState, siblings: Sequence[OpportunityState]
) -> tuple[ContactPoint, str] | None:
    ordered = (
        primary,
        *sorted(
            (state for state in siblings if state is not primary),
            key=lambda state: state.opportunity_id,
        ),
    )
    for state in ordered:
        point = resolve_contact(state)
        if point is not None:
            primary_name = (primary.contact_name or "").strip()
            return point, primary_name or (state.contact_name or "").strip()
    return None


def _candidate_id(
    contact_key: str,
    route: ContactPoint,
    scored: ScoredOpportunity,
    open_ids: Sequence[str],
    latest_outbound_at: datetime | None,
) -> UUID:
    primary_reason = scored.reasons[0]
    identity = {
        "channel": route.channel,
        "contact_address": normalize_contact_address(route),
        "contact_key": contact_key,
        "latest_outbound_at": (
            aware_utc(latest_outbound_at, "latest_outbound_at").isoformat()
            if latest_outbound_at is not None
            else None
        ),
        "open_opportunity_ids": list(open_ids),
        "primary_opportunity_id": scored.opportunity_id,
        "reason": primary_reason.reason,
        "signal_at": (
            aware_utc(primary_reason.signal_at, "signal_at").isoformat()
            if primary_reason.signal_at is not None
            else None
        ),
        "version": 2,
    }
    # Persisted UUID namespace: preserve existing candidate and draft identities.
    # This legacy protocol constant is independent of the Respawned product name.
    return uuid5(
        NAMESPACE_URL,
        "follow-up-engine:candidate:"
        + json.dumps(identity, sort_keys=True, separators=(",", ":")),
    )


def select_candidates(
    states: Sequence[OpportunityState],
    scored: Sequence[ScoredOpportunity],
    now: datetime,
    policy: Policy,
) -> list[Candidate]:
    """Collapse scores to one cooldown-safe candidate per stable contact."""

    run_at = aware_utc(now, "now")
    cooldown = Decimal(str(policy.cooldown_hours))
    if not cooldown.is_finite() or cooldown < 0:
        raise ValueError("cooldown_hours must be a non-negative finite number")
    timezone = ZoneInfo(policy.business_context.timezone_name)

    def contactable(state: OpportunityState) -> bool:
        return is_contactable_opportunity(
            state,
            run_at,
            timezone,
            policy.dead_after_days,
        )

    states_by_contact: dict[str, list[OpportunityState]] = {}
    state_by_id: dict[str, OpportunityState] = {}
    for state in states:
        state_by_id[state.opportunity_id] = state
        if key := normalize_contact_key(state.contact_key):
            states_by_contact.setdefault(key, []).append(state)

    scored_by_contact: dict[str, list[ScoredOpportunity]] = {}
    for item in scored:
        state = state_by_id.get(item.opportunity_id)
        if state is None or not contactable(state):
            continue
        if key := normalize_contact_key(state.contact_key):
            scored_by_contact.setdefault(key, []).append(item)

    candidates: list[Candidate] = []
    for contact_key, contact_scores in scored_by_contact.items():
        siblings = states_by_contact[contact_key]
        latest_outbound_at = _latest_outbound(siblings, run_at)
        if latest_outbound_at is not None and within_cooldown(
            latest_outbound_at, run_at, cooldown
        ):
            continue

        contact_scores.sort(key=lambda item: (-item.score, item.opportunity_id))
        primary = contact_scores[0]
        primary_state = state_by_id[primary.opportunity_id]
        resolved = _route(primary_state, siblings)
        if resolved is None:
            continue
        route, contact_name = resolved
        open_ids = tuple(
            sorted(
                {
                    state.opportunity_id
                    for state in siblings
                    if contactable(state)
                }
            )
        )
        other_ids = tuple(
            opportunity_id
            for opportunity_id in open_ids
            if opportunity_id != primary.opportunity_id
        )
        candidates.append(
            Candidate(
                id=_candidate_id(
                    contact_key,
                    route,
                    primary,
                    open_ids,
                    latest_outbound_at,
                ),
                run_at=run_at,
                primary_opportunity_id=primary.opportunity_id,
                contact_key=contact_key,
                contact_name=contact_name,
                reason=primary.reasons[0].reason,
                score=primary.score,
                other_opportunity_ids=other_ids,
                channel=route.channel,
                contact_address=route.address,
            )
        )

    return sorted(
        candidates,
        key=lambda item: (
            -item.score,
            item.primary_opportunity_id,
            item.contact_key,
        ),
    )
