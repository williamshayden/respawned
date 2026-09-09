"""Process candidate drafts using the operator's configured review policy."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.engine import Engine

from respawned.core.policy import Policy
from respawned.core.review import (
    ReviewBlockedError,
    authorize_draft_automatically,
    draft_candidate,
)
from respawned.core.sync import sync_candidates
from respawned.core.time import aware_utc
from respawned.llm.adapter import LiteLLMAdapter


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ProcessItem:
    candidate_id: UUID
    status: Literal["pending", "authorized", "blocked", "already_reviewed"]
    draft_id: UUID | None = None
    outbox_id: int | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessResult:
    review_mode: Literal["human", "automatic"]
    candidate_count: int
    items: tuple[ProcessItem, ...]


def process_candidates(
    engine: Engine,
    *,
    policy: Policy,
    adapter: LiteLLMAdapter,
    limit: int = 10,
    clock: Callable[[], datetime] = utc_now,
) -> ProcessResult:
    """Persist each step before continuing, so retries reuse completed work.

    Model failures block individual candidates. Unexpected failures propagate;
    earlier committed items remain visible through the draft and outbox reads.
    Human mode only creates pending drafts. Automatic mode uses the same final
    eligibility, copy, recipient, and cooldown checks as human authorization.
    Neither mode sends messages.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")

    def current_time() -> datetime:
        return aware_utc(clock(), "clock result")

    with engine.begin() as connection:
        candidates = sync_candidates(
            connection, now=current_time(), policy=policy, limit=limit
        ).candidates

    items = []
    for candidate in candidates:
        draft = None
        try:
            with engine.begin() as connection:
                draft = draft_candidate(
                    connection, candidate=candidate, now=current_time(),
                    policy=policy, adapter=adapter,
                )
            if draft is None:
                items.append(ProcessItem(candidate.id, "already_reviewed"))
                continue
            if policy.review.mode == "human":
                items.append(ProcessItem(candidate.id, "pending", draft.id))
                continue
            with engine.begin() as connection:
                outbox_id = authorize_draft_automatically(
                    connection, draft_id=draft.id,
                    expected_review_token=draft.review_token,
                    now=current_time(), policy=policy,
                )
            items.append(ProcessItem(candidate.id, "authorized", draft.id, outbox_id))
        except ReviewBlockedError as exc:
            items.append(ProcessItem(
                candidate.id, "blocked", draft.id if draft is not None else None,
                detail=str(exc),
            ))
    return ProcessResult(policy.review.mode, len(candidates), tuple(items))
