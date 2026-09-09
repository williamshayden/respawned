"""Read-only visibility of unanswered replies, independent of outreach eligibility."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.core.contact import normalize_contact_address
from respawned.core.domain import OpportunityState, resolve_contact
from respawned.core.opportunity import is_contactable_opportunity
from respawned.core.policy import Policy
from respawned.core.reduce import reduce_opportunities
from respawned.core.time import aware_utc


@dataclass(frozen=True, slots=True)
class ReplyEvidence:
    opportunity_id: str
    activity_id: str
    occurred_at: datetime
    channel: str | None


@dataclass(frozen=True, slots=True)
class ReplyInboxItem:
    contact_key: str
    contact_name: str | None
    channel: str
    contact_address: str
    opportunity_ids: tuple[str, ...]
    latest_reply_at: datetime
    last_outbound_at: datetime | None
    reply_evidence: tuple[ReplyEvidence, ...]
    pending_outbox_count: int = 0


@dataclass(frozen=True, slots=True)
class ReplyInboxResult:
    as_of: datetime
    items: tuple[ReplyInboxItem, ...]
    total: int
    has_more: bool
    source_freshness: Literal["unknown"] = "unknown"
    outreach_eligibility: Literal["not_evaluated"] = "not_evaluated"


def list_reply_inbox(
    connection: Connection,
    *,
    now: datetime,
    policy: Policy,
    limit: int = 50,
    kinds: Iterable[str] | None = None,
) -> ReplyInboxResult:
    """List ingested human replies that have no later contact-wide outbound.

    Connectors classify human replies explicitly with ``human`` classification
    and ``inbound`` direction. Legacy ``contact_replied`` events remain supported
    unless explicitly marked ``automated``; automated receipts never enter inbox.
    This is a current-state view with an activity cutoff, not historical snapshot
    reconstruction or proof that the source is up to date. Current contact routes
    are displayed, never inferred from reply evidence. An outbox reservation is
    not an answer: only ingested outbound state resolves an inbox item. Counts of
    pending reservations are contact-wide and visible at the requested cutoff.
    Optional workspace kinds select complete groups containing matching reply
    evidence, after contact-wide resolution and before the result limit.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer between 1 and 200")
    now = aware_utc(now, "list_reply_inbox.now")
    states = reduce_opportunities(connection, now)
    items = reply_inbox_items(states, now=now, policy=policy, kinds=kinds)
    selected = items[:limit]
    if selected:
        counts = dict(connection.execute(text("""
            SELECT contact_key, count(*)
            FROM outbox
            WHERE contact_key = ANY(:contact_keys)
              AND status = 'pending' AND created_at <= :now
            GROUP BY contact_key
        """), {
            "contact_keys": sorted({item.contact_key for item in selected}), "now": now,
        }).tuples().all())
        selected = [replace(item, pending_outbox_count=counts.get(item.contact_key, 0))
                    for item in selected]
    return ReplyInboxResult(now, tuple(selected), len(items), len(items) > limit)


def reply_inbox_items(
    states: Sequence[OpportunityState], *, now: datetime, policy: Policy,
    kinds: Iterable[str] | None = None,
) -> list[ReplyInboxItem]:
    """Compute complete reply groups before pagination or reservation enrichment.

    The inbox and monitoring overview share this projection, so monitoring cannot
    lose replies past the first page or ignore outbound evidence in another view.
    """
    now = aware_utc(now, "reply_inbox_items.now")
    timezone = ZoneInfo(policy.business_context.timezone_name)

    latest_outbounds: dict[str, datetime] = {}
    for state in states:
        if state.contact_key and state.last_outbound_at is not None:
            outbound = aware_utc(state.last_outbound_at, "last_outbound_at")
            latest_outbounds[state.contact_key] = max(
                outbound, latest_outbounds.get(state.contact_key, outbound)
            )

    groups: dict[tuple[str, str, str], list[tuple[OpportunityState, ReplyEvidence]]] = (
        defaultdict(list)
    )
    for state in states:
        if not state.contact_key or not is_contactable_opportunity(
            state, now, timezone, policy.dead_after_days
        ):
            continue
        route = resolve_contact(state)
        if route is None:
            continue
        outbound = latest_outbounds.get(state.contact_key)
        group_key = (state.contact_key, route.channel, normalize_contact_address(route))
        for activity in state.activities:
            if (
                (activity.activity_type == "contact_replied"
                 or activity.classification == "human")
                and activity.direction == "inbound"
                and activity.classification != "automated"
                # Equal source timestamps do not establish which came later.
                and (outbound is None or activity.occurred_at >= outbound)
            ):
                groups[group_key].append(
                    (state, ReplyEvidence(
                        state.opportunity_id,
                        activity.activity_id,
                        aware_utc(activity.occurred_at, "occurred_at"),
                        activity.channel,
                    ))
                )

    items: list[ReplyInboxItem] = []
    selected_kinds = set(kinds or ())
    for (contact_key, channel, _address), grouped in groups.items():
        grouped.sort(key=lambda pair: (
            pair[1].occurred_at, pair[1].opportunity_id, pair[1].activity_id
        ))
        primary, latest = grouped[-1]
        route = resolve_contact(primary)
        assert route is not None
        if selected_kinds and not any(state.kind in selected_kinds for state, _ in grouped):
            continue
        items.append(ReplyInboxItem(
            contact_key=contact_key,
            contact_name=primary.contact_name,
            channel=channel,
            contact_address=route.address,
            opportunity_ids=tuple(sorted({item.opportunity_id for _, item in grouped})),
            latest_reply_at=latest.occurred_at,
            last_outbound_at=latest_outbounds.get(contact_key),
            reply_evidence=tuple(item for _, item in grouped),
        ))
    items.sort(key=lambda item: (
        -item.latest_reply_at.timestamp(), item.contact_key, item.channel,
        item.contact_address,
    ))
    return items
