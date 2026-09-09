"""Application service for computing and persisting follow-up candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.core.candidates import Candidate, select_candidates
from respawned.core.domain import OpportunityState
from respawned.core.policy import Policy
from respawned.core.reasons import ReasonFactory
from respawned.core.reduce import reduce_opportunities
from respawned.core.score import score_opportunities


INSERT_SYNC_RUN = text(
    """
    INSERT INTO sync_runs (id, run_at, candidate_count, created_at)
    VALUES (:id, :run_at, :candidate_count, clock_timestamp())
    """
)

INSERT_CANDIDATE = text(
    """
    INSERT INTO candidates (
        id,
        sync_run_id,
        run_at,
        primary_opportunity_id,
        contact_key,
        contact_address,
        contact_name,
        channel,
        reason,
        score,
        other_opportunity_ids
    ) VALUES (
        :id,
        :sync_run_id,
        :run_at,
        :primary_opportunity_id,
        :contact_key,
        :contact_address,
        :contact_name,
        :channel,
        :reason,
        :score,
        :other_opportunity_ids
    )
    ON CONFLICT (id) DO NOTHING
    RETURNING id
    """
)

UPDATE_CANDIDATE = text(
    """
    UPDATE candidates AS candidate
    SET
        sync_run_id = :sync_run_id,
        run_at = :run_at,
        primary_opportunity_id = :primary_opportunity_id,
        contact_key = :contact_key,
        contact_address = :contact_address,
        contact_name = :contact_name,
        channel = :channel,
        reason = :reason,
        score = :score,
        other_opportunity_ids = :other_opportunity_ids
    FROM sync_runs AS incoming_run, sync_runs AS current_run
    WHERE candidate.id = :id
      AND incoming_run.id = :sync_run_id
      AND current_run.id = candidate.sync_run_id
      AND (
          incoming_run.run_at,
          incoming_run.created_at,
          incoming_run.id
      ) >= (
          current_run.run_at,
          current_run.created_at,
          current_run.id
      )
    """
)

EXISTING_CANDIDATES = text(
    """
    SELECT candidates.id, drafts.status AS draft_status
    FROM candidates
    LEFT JOIN drafts ON drafts.candidate_id = candidates.id
    WHERE candidates.id = ANY(:candidate_ids)
    """
)

RESERVED_CONTACTS = text(
    """
    SELECT DISTINCT contact_key FROM outbox
    WHERE contact_key = ANY(:contact_keys)
      AND created_at > :now - make_interval(
          secs => CAST(:cooldown_seconds AS double precision)
      )
    """
)


@dataclass(frozen=True, slots=True)
class SyncResult:
    candidates: tuple[Candidate, ...]
    inserted_count: int
    run_id: UUID | None
    dry_run: bool


def compute_candidates(
    states: Sequence[OpportunityState],
    policy: Policy,
    now: datetime,
    *,
    limit: int = 10,
    evaluators: Mapping[str, ReasonFactory] | None = None,
) -> list[Candidate]:
    """Score, contact-group, then limit one canonical state snapshot."""

    _validate_limit(limit)
    scored = score_opportunities(states, policy, now, evaluators=evaluators)
    return select_candidates(
        states,
        scored,
        now,
        policy,
    )[:limit]


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer")


def _candidate_values(candidate: Candidate, run_id: UUID) -> dict[str, object]:
    return {
        "id": candidate.id,
        "sync_run_id": run_id,
        "run_at": candidate.run_at,
        "primary_opportunity_id": candidate.primary_opportunity_id,
        "contact_key": candidate.contact_key,
        "contact_address": candidate.contact_address,
        "contact_name": candidate.contact_name,
        "channel": candidate.channel,
        "reason": candidate.reason,
        "score": candidate.score,
        "other_opportunity_ids": list(candidate.other_opportunity_ids),
    }


def sync_candidates(
    conn: Connection,
    *,
    now: datetime,
    policy: Policy,
    dry_run: bool = False,
    limit: int = 10,
    evaluators: Mapping[str, ReasonFactory] | None = None,
    primary_opportunity_id: str | None = None,
) -> SyncResult:
    """Run one idempotent sync, optionally persisting only a selected primary.

    Selection happens after global eligibility, contact grouping, and review
    history. It cannot promote an ineligible sibling or bypass a cooldown.
    """

    _validate_limit(limit)
    states = reduce_opportunities(conn, now)
    # One candidate per contact means the state count bounds the complete queue.
    # Review history and reservations must be considered before the user limit.
    ranked = compute_candidates(
        states, policy, now, limit=len(states), evaluators=evaluators
    )
    existing: dict[UUID, str | None] = {}
    reserved_contacts: set[str] = set()
    if ranked:
        existing = {
            row["id"]: row["draft_status"]
            for row in conn.execute(
                EXISTING_CANDIDATES,
                {"candidate_ids": [candidate.id for candidate in ranked]},
            ).mappings()
        }
        reserved_contacts = set(
            conn.execute(
                RESERVED_CONTACTS,
                {
                    "contact_keys": [candidate.contact_key for candidate in ranked],
                    "now": now,
                    "cooldown_seconds": policy.cooldown_hours * 3600,
                },
            ).scalars()
        )
    candidates = tuple(
        candidate
        for candidate in ranked
        if existing.get(candidate.id) not in {"approved", "rejected"}
        and candidate.contact_key not in reserved_contacts
        and (primary_opportunity_id is None
             or candidate.primary_opportunity_id == primary_opportunity_id)
    )[:limit]
    if dry_run:
        inserted_count = sum(
            candidate.id not in existing
            for candidate in candidates
        )
        return SyncResult(candidates, inserted_count, None, True)

    run_id = uuid4()
    conn.execute(
        INSERT_SYNC_RUN,
        {
            "id": run_id,
            "run_at": now,
            "candidate_count": len(candidates),
        },
    )
    inserted_count = 0
    # Independent syncs can rank the same IDs differently. A stable lock order
    # prevents those overlapping writes from deadlocking on candidate rows.
    for candidate in sorted(candidates, key=lambda candidate: candidate.id):
        values = _candidate_values(candidate, run_id)
        inserted_count += (
            conn.execute(INSERT_CANDIDATE, values).scalar_one_or_none() is not None
        )
        conn.execute(UPDATE_CANDIDATE, values)

    return SyncResult(candidates, inserted_count, run_id, False)
