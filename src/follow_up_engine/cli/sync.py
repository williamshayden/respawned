"""Compute and persist the current top follow-up candidates."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from follow_up_engine.core.candidates import Candidate, select_candidates
from follow_up_engine.core.reduce import QuoteState, reduce_quotes
from follow_up_engine.core.score import Policy, load_policy, score_quotes
from follow_up_engine.db.helpers.pg_connect import create_tables, get_engine


DEFAULT_POLICY_PATH = Path(__file__).parents[1] / "config" / "policy.yaml"

INSERT_SYNC_RUN = text(
    """
    INSERT INTO sync_runs (id, run_at, candidate_count)
    VALUES (:id, :run_at, :candidate_count)
    """
)

INSERT_CANDIDATE = text(
    """
    INSERT INTO candidates (
        id,
        sync_run_id,
        run_at,
        primary_quote_id,
        customer_phone,
        reason,
        score,
        other_quote_ids
    ) VALUES (
        :id,
        :sync_run_id,
        :run_at,
        :primary_quote_id,
        :customer_phone,
        :reason,
        :score,
        :other_quote_ids
    )
    ON CONFLICT (id) DO NOTHING
    RETURNING id
    """
)

UPDATE_CANDIDATE = text(
    """
    UPDATE candidates
    SET
        sync_run_id = :sync_run_id,
        run_at = :run_at,
        primary_quote_id = :primary_quote_id,
        customer_phone = :customer_phone,
        reason = :reason,
        score = :score,
        other_quote_ids = :other_quote_ids
    WHERE id = :id
    """
)


@dataclass(frozen=True, slots=True)
class SyncResult:
    candidates: tuple[Candidate, ...]
    inserted_count: int
    run_id: UUID | None
    dry_run: bool


def compute_candidates(
    states: Sequence[QuoteState],
    policy: Policy,
    now: datetime,
    *,
    limit: int = 10,
) -> list[Candidate]:
    """Score, customer-group, then limit a reduced state snapshot."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    scored = score_quotes(states, policy, now)
    selected = select_candidates(
        states,
        scored,
        now,
        policy.cooldown_hours,
    )
    return selected[:limit]


def sync_candidates(
    conn: Connection,
    *,
    now: datetime,
    policy: Policy,
    dry_run: bool = False,
    limit: int = 10,
) -> SyncResult:
    """Run one idempotent candidate sync in the caller's transaction."""
    states = reduce_quotes(
        conn,
        now,
        business_context=policy.business_context,
    )
    candidates = tuple(compute_candidates(states, policy, now, limit=limit))
    if dry_run:
        return SyncResult(
            candidates=candidates,
            inserted_count=0,
            run_id=None,
            dry_run=True,
        )

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
    for candidate in candidates:
        candidate_values = {
            "id": candidate.id,
            "sync_run_id": run_id,
            "run_at": candidate.run_at,
            "primary_quote_id": candidate.primary_quote_id,
            "customer_phone": candidate.customer_phone,
            "reason": candidate.reason,
            "score": candidate.score,
            "other_quote_ids": list(candidate.other_quote_ids),
        }
        result = conn.execute(
            INSERT_CANDIDATE,
            candidate_values,
        )
        inserted_count += result.scalar_one_or_none() is not None
        conn.execute(UPDATE_CANDIDATE, candidate_values)

    return SyncResult(
        candidates=candidates,
        inserted_count=inserted_count,
        run_id=run_id,
        dry_run=False,
    )


def _parse_now(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--now must include a UTC offset")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="follow-up-engine sync")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--now", type=_parse_now)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    args = parser.parse_args(argv)

    now = args.now or datetime.now(UTC)
    policy = load_policy(args.policy)
    engine = get_engine()
    create_tables(engine)
    try:
        with engine.begin() as conn:
            result = sync_candidates(
                conn,
                now=now,
                policy=policy,
                dry_run=args.dry_run,
                limit=args.limit,
            )
    finally:
        engine.dispose()

    action = "would add" if result.dry_run else "added"
    print(
        f"Selected {len(result.candidates)} candidates; "
        f"{action} {result.inserted_count} new opportunities."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
