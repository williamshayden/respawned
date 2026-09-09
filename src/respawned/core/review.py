"""Transactional drafting and explicit human or policy-based authorization."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.core.candidates import Candidate
from respawned.core.contact import (
    lock_contact_keys,
    lock_contact_opportunities,
    normalize_contact_address,
    normalize_contact_key,
)
from respawned.core.domain import ContactPoint, OpportunityState, resolve_contact
from respawned.core.draft import draft_follow_up
from respawned.core.helpers.payload import DraftPayload
from respawned.core.helpers.validate import DraftValidationError, validate_draft
from respawned.core.opportunity import is_contactable_opportunity
from respawned.core.policy import Policy
from respawned.core.reduce import reduce_opportunities
from respawned.core.time import aware_utc, within_cooldown
from respawned.llm.adapter import LiteLLMAdapter, LLMAdapterError

LATEST_CANDIDATES_QUERY = text(
    """
    WITH latest_run AS (
        SELECT id FROM sync_runs
        ORDER BY run_at DESC, created_at DESC, id DESC
        LIMIT 1
    )
    SELECT
        candidates.id, candidates.run_at,
        candidates.primary_opportunity_id, candidates.contact_key,
        candidates.contact_address, candidates.contact_name,
        candidates.channel, candidates.reason, candidates.score,
        candidates.other_opportunity_ids
    FROM candidates
    JOIN latest_run ON latest_run.id = candidates.sync_run_id
    LEFT JOIN drafts ON drafts.candidate_id = candidates.id
    WHERE drafts.id IS NULL OR drafts.status = 'pending'
    ORDER BY candidates.score DESC,
             candidates.primary_opportunity_id,
             candidates.contact_key
    """
)

OUTBOX_COOLDOWN_QUERY = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM outbox
        WHERE contact_key = :contact_key
          AND draft_id <> :draft_id
          AND created_at > :now - make_interval(
              secs => CAST(:cooldown_seconds AS double precision)
          )
    )
    """
)


class ReviewBlockedError(RuntimeError):
    """Raised when a draft cannot safely advance through authorization."""


@dataclass(frozen=True, slots=True)
class PersistedDraft:
    id: UUID
    candidate_id: UUID
    contact_key: str
    contact_address: str
    contact_name: str | None
    channel: str
    primary_opportunity_id: str
    opportunity_ids: tuple[str, ...]
    body: str
    status: str
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None

    @property
    def review_token(self) -> str:
        """Identify the displayed copy and destination, not human authorization."""
        snapshot = {
            "version": 1,
            "draft_id": str(self.id),
            "body": self.body,
            "contact_key": self.contact_key,
            "contact_address": self.contact_address,
            "contact_name": self.contact_name,
            "channel": self.channel,
            "primary_opportunity_id": self.primary_opportunity_id,
            "opportunity_ids": self.opportunity_ids,
        }
        return sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class CurrentReviewState:
    states: tuple[OpportunityState, ...]
    primary: OpportunityState
    opportunity_ids: tuple[str, ...]
    contact_name: str | None


def rank_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda item: (-item.score, item.primary_opportunity_id, item.contact_key),
    )


def _row_to_draft(row: Any) -> PersistedDraft:
    values = dict(row)
    values["opportunity_ids"] = tuple(values["opportunity_ids"] or ())
    return PersistedDraft(**values)


def load_latest_candidates(connection: Connection) -> list[Candidate]:
    candidates = []
    for row in connection.execute(LATEST_CANDIDATES_QUERY).mappings():
        values = dict(row)
        values["other_opportunity_ids"] = tuple(values["other_opportunity_ids"] or ())
        candidates.append(Candidate(**values))
    return candidates


def _load_candidate_draft(
    connection: Connection, candidate_id: UUID
) -> PersistedDraft | None:
    row = (
        connection.execute(
            text(
                """
            SELECT id, candidate_id, contact_key, contact_address, contact_name,
                   channel, primary_opportunity_id, opportunity_ids, body,
                   status, created_at, updated_at, reviewed_at
            FROM drafts WHERE candidate_id = :candidate_id
            """
            ),
            {"candidate_id": candidate_id},
        )
        .mappings()
        .one_or_none()
    )
    return _row_to_draft(row) if row is not None else None


def _load_draft(
    connection: Connection,
    draft_id: UUID | str,
    *,
    for_update: bool = False,
) -> PersistedDraft:
    lock = " FOR UPDATE" if for_update else ""
    row = (
        connection.execute(
            text(
                """
            SELECT id, candidate_id, contact_key, contact_address, contact_name,
                   channel, primary_opportunity_id, opportunity_ids, body,
                   status, created_at, updated_at, reviewed_at
            FROM drafts WHERE id = :draft_id
            """
                + lock
            ),
            {"draft_id": draft_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ReviewBlockedError(f"draft {draft_id!s} does not exist")
    return _row_to_draft(row)


def _current_route(
    primary: OpportunityState, siblings: Sequence[OpportunityState]
) -> tuple[ContactPoint, OpportunityState] | None:
    ordered = (
        primary,
        *sorted(
            (state for state in siblings if state is not primary),
            key=lambda state: state.opportunity_id,
        ),
    )
    for state in ordered:
        if route := resolve_contact(state):
            return route, state
    return None


def _current_review_state(
    connection: Connection,
    *,
    contact_key: str,
    channel: str,
    contact_address: str,
    primary_opportunity_id: str,
    opportunity_ids: Sequence[str],
    fallback_contact_name: str | None,
    now: datetime,
    policy: Policy,
) -> CurrentReviewState:
    key = normalize_contact_key(contact_key)
    if key is None:
        raise ReviewBlockedError("contact identity is no longer usable")
    try:
        expected_route = ContactPoint(channel, contact_address)
    except ValueError as exc:
        raise ReviewBlockedError("contact route is no longer usable") from exc

    states = tuple(reduce_opportunities(connection, now, contact_key=key))
    by_id = {state.opportunity_id: state for state in states}
    primary = by_id.get(primary_opportunity_id)
    ids = tuple(dict.fromkeys((primary_opportunity_id, *opportunity_ids)))
    timezone = ZoneInfo(policy.business_context.timezone_name)
    for opportunity_id in ids:
        state = by_id.get(opportunity_id)
        if (
            state is None
            or not is_contactable_opportunity(
                state, now, timezone, policy.dead_after_days
            )
            or normalize_contact_key(state.contact_key) != key
        ):
            raise ReviewBlockedError(
                f"opportunity {opportunity_id!r} is no longer open/contactable"
            )
    assert primary is not None

    siblings = [
        state for state in states if normalize_contact_key(state.contact_key) == key
    ]
    resolved = _current_route(primary, siblings)
    if resolved is None:
        raise ReviewBlockedError("contact route is no longer usable")
    actual_route, route_state = resolved
    if actual_route.channel != expected_route.channel or normalize_contact_address(
        actual_route
    ) != normalize_contact_address(expected_route):
        raise ReviewBlockedError(
            "contact route changed; run sync before drafting or approving"
        )
    contact_name = (
        (primary.contact_name or "").strip()
        or (route_state.contact_name or "").strip()
        or (fallback_contact_name or "").strip()
        or None
    )
    return CurrentReviewState(states, primary, ids, contact_name)


def draft_candidate(
    connection: Connection,
    *,
    candidate: Candidate,
    now: datetime,
    policy: Policy,
    adapter: LiteLLMAdapter,
) -> PersistedDraft | None:
    """Lazily persist safe copy for one current candidate."""
    now = aware_utc(now, "now")
    existing = _load_candidate_draft(connection, candidate.id)
    if existing is not None and existing.status != "pending":
        return None
    current = _current_review_state(
        connection,
        contact_key=candidate.contact_key,
        channel=candidate.channel,
        contact_address=candidate.contact_address,
        primary_opportunity_id=candidate.primary_opportunity_id,
        opportunity_ids=candidate.other_opportunity_ids,
        fallback_contact_name=candidate.contact_name,
        now=now,
        policy=policy,
    )
    if existing is not None:
        return existing

    reason = policy.reasons.get(candidate.reason)
    if reason is None:
        raise ReviewBlockedError(
            f"candidate reason {candidate.reason!r} is not configured"
        )
    primary = current.primary
    try:
        body = draft_follow_up(
            DraftPayload(
                contact_name=current.contact_name,
                owner_name=primary.owner_name,
                tone=reason.tone,
                other_open_opportunity_count=len(current.opportunity_ids) - 1,
                max_characters=policy.drafting.max_characters,
                sign_off=policy.drafting.sign_off,
                require_owner_name=policy.drafting.require_owner_name,
                kind=primary.kind,
                title=primary.title,
                company=primary.context.company,
                role=primary.context.role,
                stage=primary.context.stage,
                summary=primary.context.summary,
            ),
            opportunity_status=primary.status or "",
            adapter=adapter,
        )
    except DraftValidationError as exc:
        raise ReviewBlockedError(f"draft failed validation: {exc}") from exc
    except LLMAdapterError as exc:
        raise ReviewBlockedError(f"draft generation failed: {exc}") from exc
    connection.execute(
        text(
            """
            INSERT INTO drafts (
                id, candidate_id, contact_key, contact_address, contact_name,
                channel, primary_opportunity_id, opportunity_ids, body,
                status, created_at, updated_at
            ) VALUES (
                :id, :candidate_id, :contact_key, :contact_address, :contact_name,
                :channel, :primary_opportunity_id, :opportunity_ids, :body,
                'pending', :now, :now
            ) ON CONFLICT (candidate_id) DO NOTHING
            """
        ),
        {
            "id": uuid4(),
            "candidate_id": candidate.id,
            "contact_key": candidate.contact_key,
            "contact_address": candidate.contact_address,
            "contact_name": current.contact_name,
            "channel": candidate.channel,
            "primary_opportunity_id": candidate.primary_opportunity_id,
            "opportunity_ids": list(current.opportunity_ids),
            "body": body,
            "now": now,
        },
    )
    persisted = _load_candidate_draft(connection, candidate.id)
    return (
        persisted if persisted is not None and persisted.status == "pending" else None
    )


def iter_candidate_drafts(
    connection: Connection,
    *,
    candidates: Sequence[Candidate],
    now: datetime,
    policy: Policy,
    adapter: LiteLLMAdapter,
) -> Iterator[PersistedDraft]:
    for candidate in rank_candidates(candidates):
        draft = draft_candidate(
            connection, candidate=candidate, now=now, policy=policy, adapter=adapter
        )
        if draft is not None:
            yield draft


def _has_state_cooldown(
    states: Sequence[OpportunityState],
    *,
    contact_key: str,
    now: datetime,
    cooldown_hours: Decimal,
) -> bool:
    return any(
        state.last_outbound_at is not None
        and normalize_contact_key(state.contact_key) == contact_key
        and within_cooldown(state.last_outbound_at, now, cooldown_hours)
        for state in states
    )


def _has_outbox_reservation(
    connection: Connection,
    *,
    draft_id: UUID,
    contact_key: str,
    now: datetime,
    cooldown_hours: Decimal,
) -> bool:
    return bool(
        connection.execute(
            OUTBOX_COOLDOWN_QUERY,
            {
                "draft_id": draft_id,
                "contact_key": contact_key,
                "now": now,
                "cooldown_seconds": cooldown_hours * Decimal(3600),
            },
        ).scalar_one()
    )


def _validate_body(body: str, primary: OpportunityState, policy: Policy) -> str:
    try:
        return validate_draft(
            body,
            max_characters=policy.drafting.max_characters,
            opportunity_status=primary.status or "",
            owner_name=primary.owner_name,
            require_owner_name=policy.drafting.require_owner_name,
        )
    except DraftValidationError as exc:
        raise ReviewBlockedError(f"draft failed validation: {exc}") from exc


def _require_review_snapshot(draft: PersistedDraft, expected_review_token: str) -> None:
    if draft.review_token != expected_review_token:
        raise ReviewBlockedError(
            "draft changed since it was shown; review the latest copy before continuing"
        )


def enqueue_outbox(
    connection: Connection,
    *,
    draft_id: UUID | str,
    contact_key: str,
    contact_address: str,
    contact_name: str | None,
    channel: str,
    opportunity_ids: Sequence[str],
    body: str,
    created_at: datetime | None = None,
    authorization_mode: Literal[
        "human", "automatic", "legacy_unknown"
    ] = "legacy_unknown",
) -> int:
    """Reserve one immutable delivery row for a draft."""
    values: dict[str, object] = {
        "draft_id": draft_id,
        "contact_key": contact_key,
        "contact_address": contact_address,
        "contact_name": contact_name,
        "channel": channel,
        "opportunity_ids": list(opportunity_ids),
        "body": body,
        "authorization_mode": authorization_mode,
    }
    columns = list(values)
    if created_at is not None:
        values["created_at"] = created_at
        columns.append("created_at")
    inserted = connection.execute(
        text(
            "INSERT INTO outbox ({columns}) VALUES ({values}) "
            "ON CONFLICT (draft_id) DO NOTHING RETURNING id".format(
                columns=", ".join(columns),
                values=", ".join(f":{column}" for column in columns),
            )
        ),
        values,
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted
    return connection.execute(
        text("SELECT id FROM outbox WHERE draft_id = :draft_id"),
        {"draft_id": draft_id},
    ).scalar_one()


def approve_draft(
    connection: Connection,
    *,
    draft_id: UUID | str,
    expected_review_token: str,
    now: datetime,
    policy: Policy,
) -> int:
    """Reserve the exact reviewed copy after rechecking live safety constraints."""
    return _authorize_draft(
        connection,
        draft_id=draft_id,
        expected_review_token=expected_review_token,
        now=now,
        policy=policy,
        authorization_mode="human",
    )


def authorize_draft_automatically(
    connection: Connection,
    *,
    draft_id: UUID | str,
    expected_review_token: str,
    now: datetime,
    policy: Policy,
) -> int:
    """Reserve validated copy only under explicit trusted automatic policy.

    This records policy authorization, not human review. Like manual approval,
    callers must commit their transaction before acknowledging the reservation.
    """
    if policy.review.mode != "automatic":
        raise ReviewBlockedError(
            "automatic authorization requires review.mode=automatic"
        )
    return _authorize_draft(
        connection,
        draft_id=draft_id,
        expected_review_token=expected_review_token,
        now=now,
        policy=policy,
        authorization_mode="automatic",
    )


def _authorize_draft(
    connection: Connection,
    *,
    draft_id: UUID | str,
    expected_review_token: str,
    now: datetime,
    policy: Policy,
    authorization_mode: Literal["human", "automatic"],
) -> int:
    now = aware_utc(now, "now")
    unlocked = _load_draft(connection, draft_id)
    contact_key = normalize_contact_key(unlocked.contact_key)
    if contact_key is None:
        raise ReviewBlockedError("draft contact identity is no longer usable")
    lock_contact_opportunities(connection, contact_key)
    lock_contact_keys(connection, (contact_key,))
    draft = _load_draft(connection, draft_id, for_update=True)
    _require_review_snapshot(draft, expected_review_token)
    if normalize_contact_key(draft.contact_key) != contact_key:
        raise ReviewBlockedError("draft contact identity changed during approval")
    existing = connection.execute(
        text("SELECT id FROM outbox WHERE draft_id = :draft_id"),
        {"draft_id": draft.id},
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    if draft.status != "pending":
        raise ReviewBlockedError(f"draft {draft.id!s} has already been {draft.status}")

    current = _current_review_state(
        connection,
        contact_key=draft.contact_key,
        channel=draft.channel,
        contact_address=draft.contact_address,
        primary_opportunity_id=draft.primary_opportunity_id,
        opportunity_ids=draft.opportunity_ids,
        fallback_contact_name=draft.contact_name,
        now=now,
        policy=policy,
    )
    _validate_body(draft.body, current.primary, policy)
    if _has_state_cooldown(
        current.states,
        contact_key=contact_key,
        now=now,
        cooldown_hours=policy.cooldown_hours,
    ) or _has_outbox_reservation(
        connection,
        draft_id=draft.id,
        contact_key=contact_key,
        now=now,
        cooldown_hours=policy.cooldown_hours,
    ):
        raise ReviewBlockedError(
            "contact-wide cooldown was consumed after this draft was created"
        )

    outbox_id = enqueue_outbox(
        connection,
        draft_id=draft.id,
        contact_key=contact_key,
        contact_address=draft.contact_address,
        contact_name=current.contact_name,
        channel=draft.channel,
        opportunity_ids=draft.opportunity_ids,
        body=draft.body,
        created_at=now,
        authorization_mode=authorization_mode,
    )
    connection.execute(
        text(
            """
            UPDATE drafts SET status = 'approved', updated_at = :now,
                              reviewed_at = :reviewed_at
            WHERE id = :draft_id
            """
        ),
        {
            "draft_id": draft.id,
            "now": now,
            "reviewed_at": now if authorization_mode == "human" else None,
        },
    )
    return outbox_id


def reject_draft(
    connection: Connection,
    *,
    draft_id: UUID | str,
    expected_review_token: str,
    now: datetime,
) -> PersistedDraft:
    now = aware_utc(now, "now")
    draft = _load_draft(connection, draft_id, for_update=True)
    _require_review_snapshot(draft, expected_review_token)
    if draft.status == "approved":
        raise ReviewBlockedError(f"draft {draft.id!s} has already been approved")
    if draft.status == "pending":
        connection.execute(
            text(
                """
                UPDATE drafts SET status = 'rejected', updated_at = :now,
                                  reviewed_at = :now
                WHERE id = :draft_id
                """
            ),
            {"draft_id": draft.id, "now": now},
        )
    return _load_draft(connection, draft.id)


def update_draft_message(
    connection: Connection,
    *,
    draft_id: UUID | str,
    expected_review_token: str,
    body: str,
    now: datetime,
    policy: Policy,
) -> PersistedDraft:
    now = aware_utc(now, "now")
    draft = _load_draft(connection, draft_id, for_update=True)
    _require_review_snapshot(draft, expected_review_token)
    if draft.status != "pending":
        raise ReviewBlockedError(f"draft {draft.id!s} has already been {draft.status}")
    current = _current_review_state(
        connection,
        contact_key=draft.contact_key,
        channel=draft.channel,
        contact_address=draft.contact_address,
        primary_opportunity_id=draft.primary_opportunity_id,
        opportunity_ids=draft.opportunity_ids,
        fallback_contact_name=draft.contact_name,
        now=now,
        policy=policy,
    )
    normalized = _validate_body(body, current.primary, policy)
    connection.execute(
        text("UPDATE drafts SET body = :body, updated_at = :now WHERE id = :draft_id"),
        {"draft_id": draft.id, "body": normalized, "now": now},
    )
    return _load_draft(connection, draft.id)
