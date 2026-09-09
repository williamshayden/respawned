"""Persist, review, and safely approve candidate follow-up drafts."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from respawned.cli.outbox import enqueue_outbox
from respawned.cli.ui import DEFAULT_CONSOLE, prompt_choice
from respawned.core.candidates import Candidate
from respawned.core.draft import draft_follow_up
from respawned.core.helpers.payload import DraftPayload
from respawned.core.helpers.validate import validate_draft
from respawned.core.reduce import QuoteState, reduce_quotes
from respawned.core.score import Policy, load_policy
from respawned.db.helpers.pg_connect import create_tables, get_engine
from respawned.llm.adapter import LiteLLMAdapter


DEFAULT_POLICY_PATH = Path(__file__).parents[1] / "config" / "policy.yaml"

LATEST_CANDIDATES_QUERY = text(
    """
    WITH latest_run AS (
        SELECT id
        FROM sync_runs
        ORDER BY run_at DESC, created_at DESC, id DESC
        LIMIT 1
    )
    SELECT
        candidates.id,
        candidates.run_at,
        candidates.primary_quote_id,
        candidates.customer_phone,
        candidates.customer_name,
        candidates.channel,
        candidates.reason,
        candidates.score,
        candidates.other_quote_ids
    FROM candidates
    JOIN latest_run ON latest_run.id = candidates.sync_run_id
    LEFT JOIN drafts ON drafts.candidate_id = candidates.id
    WHERE drafts.id IS NULL OR drafts.status = 'pending'
    ORDER BY
        candidates.score DESC,
        candidates.primary_quote_id,
        candidates.customer_phone
    """
)

ADVISORY_PHONE_LOCK_QUERY = text(
    "SELECT pg_advisory_xact_lock(hashtextextended(:phone_key, 0))"
)


class ReviewBlockedError(RuntimeError):
    """Raised when a draft cannot safely advance through human review."""


@dataclass(frozen=True, slots=True)
class PersistedDraft:
    id: UUID
    candidate_id: UUID
    customer_phone: str
    primary_quote_id: str
    quote_ids: tuple[str, ...]
    body: str
    status: str
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None
    channel: str = "sms"


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    presented: int = 0
    approved: int = 0
    rejected: int = 0
    skipped: int = 0
    blocked: int = 0


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _phone_key(phone: str | None) -> str | None:
    if phone is None:
        return None
    digits = "".join(character for character in phone if character in "0123456789")
    return digits or None


def _is_open(state: QuoteState) -> bool:
    return isinstance(state.status, str) and state.status.strip().lower() == "open"


def _rank_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.score,
            candidate.primary_quote_id,
            candidate.customer_phone,
        ),
    )


def _row_to_draft(row: Any) -> PersistedDraft:
    values = dict(row)
    values["quote_ids"] = tuple(values["quote_ids"] or ())
    return PersistedDraft(**values)


def load_latest_candidates(connection: Connection) -> list[Candidate]:
    """Load the latest sync run's candidates in deterministic priority order."""
    candidates: list[Candidate] = []
    for row in connection.execute(LATEST_CANDIDATES_QUERY).mappings():
        values = dict(row)
        values["other_quote_ids"] = tuple(values["other_quote_ids"] or ())
        candidates.append(Candidate(**values))
    return candidates


def _load_candidate_draft(
    connection: Connection,
    candidate_id: UUID,
) -> PersistedDraft | None:
    row = connection.execute(
        text(
            """
            SELECT
                id, candidate_id, customer_phone, primary_quote_id,
                quote_ids, body, status, created_at, updated_at, reviewed_at,
                channel
            FROM drafts
            WHERE candidate_id = :candidate_id
            """
        ),
        {"candidate_id": candidate_id},
    ).mappings().one_or_none()
    return _row_to_draft(row) if row is not None else None


def _load_draft(
    connection: Connection,
    draft_id: UUID | str,
    *,
    for_update: bool = False,
) -> PersistedDraft:
    lock_clause = " FOR UPDATE" if for_update else ""
    row = connection.execute(
        text(
            """
            SELECT
                id, candidate_id, customer_phone, primary_quote_id,
                quote_ids, body, status, created_at, updated_at, reviewed_at,
                channel
            FROM drafts
            WHERE id = :draft_id
            """
            + lock_clause
        ),
        {"draft_id": draft_id},
    ).mappings().one_or_none()
    if row is None:
        raise ReviewBlockedError(f"draft {draft_id!s} does not exist")
    return _row_to_draft(row)


def _candidate_open_states(
    connection: Connection,
    *,
    candidate: Candidate,
    now: datetime,
    policy: Policy,
) -> tuple[QuoteState, tuple[str, ...]] | None:
    candidate_phone_key = _phone_key(candidate.customer_phone)
    if candidate_phone_key is None:
        return None

    states = reduce_quotes(
        connection,
        now,
        business_context=policy.business_context,
    )
    state_by_id = {state.quote_id: state for state in states}
    primary = state_by_id.get(candidate.primary_quote_id)
    if (
        primary is None
        or not _is_open(primary)
        or _phone_key(primary.customer_phone) != candidate_phone_key
    ):
        return None

    quote_ids = [primary.quote_id]
    for quote_id in candidate.other_quote_ids:
        state = state_by_id.get(quote_id)
        if (
            state is not None
            and _is_open(state)
            and _phone_key(state.customer_phone) == candidate_phone_key
            and quote_id not in quote_ids
        ):
            quote_ids.append(quote_id)
    return primary, tuple(quote_ids)


def draft_candidate(
    connection: Connection,
    *,
    candidate: Candidate,
    now: datetime,
    policy: Policy,
    adapter: LiteLLMAdapter,
) -> PersistedDraft | None:
    """Lazily persist safe copy for one currently contactable candidate."""
    now = _aware_utc(now, "now")
    existing = _load_candidate_draft(connection, candidate.id)
    if existing is not None:
        return existing if existing.status == "pending" else None

    current = _candidate_open_states(
        connection,
        candidate=candidate,
        now=now,
        policy=policy,
    )
    if current is None:
        return None
    primary, quote_ids = current

    reason_policy = policy.reasons.get(candidate.reason)
    if reason_policy is None:
        raise ReviewBlockedError(
            f"candidate reason {candidate.reason!r} is not configured"
        )
    body = draft_follow_up(
        DraftPayload(
            customer_name=primary.customer_name,
            tech_name=primary.tech_name,
            tone=reason_policy.tone,
            other_open_quote_count=len(quote_ids) - 1,
            max_characters=policy.drafting.max_characters,
            sign_off=policy.drafting.sign_off,
            require_tech_name=policy.drafting.require_tech_name,
        ),
        quote_status="open",
        adapter=adapter,
    )
    draft_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO drafts (
                id, candidate_id, customer_phone, primary_quote_id,
                quote_ids, body, status, created_at, updated_at, channel
            ) VALUES (
                :id, :candidate_id, :customer_phone, :primary_quote_id,
                :quote_ids, :body, 'pending', :now, :now, :channel
            )
            ON CONFLICT (candidate_id) DO NOTHING
            """
        ),
        {
            "id": draft_id,
            "candidate_id": candidate.id,
            "customer_phone": candidate.customer_phone,
            "primary_quote_id": candidate.primary_quote_id,
            "quote_ids": list(quote_ids),
            "body": body,
            "now": now,
            "channel": candidate.channel,
        },
    )
    persisted = _load_candidate_draft(connection, candidate.id)
    if persisted is None or persisted.status != "pending":
        return None
    return persisted


def iter_candidate_drafts(
    connection: Connection,
    *,
    candidates: Sequence[Candidate],
    now: datetime,
    policy: Policy,
    adapter: LiteLLMAdapter,
) -> Iterator[PersistedDraft]:
    """Generate at most one draft at a time, highest-priority candidate first."""
    for candidate in _rank_candidates(candidates):
        draft = draft_candidate(
            connection,
            candidate=candidate,
            now=now,
            policy=policy,
            adapter=adapter,
        )
        if draft is not None:
            yield draft


def _current_open_draft_states(
    connection: Connection,
    *,
    draft: PersistedDraft,
    now: datetime,
    policy: Policy,
) -> tuple[list[QuoteState], QuoteState]:
    states = reduce_quotes(
        connection,
        now,
        business_context=policy.business_context,
    )
    state_by_id = {state.quote_id: state for state in states}
    phone_key = _phone_key(draft.customer_phone)
    for quote_id in draft.quote_ids:
        state = state_by_id.get(quote_id)
        if (
            state is None
            or not _is_open(state)
            or _phone_key(state.customer_phone) != phone_key
        ):
            raise ReviewBlockedError(
                f"quote {quote_id!r} is no longer open/contactable"
            )
    primary = state_by_id.get(draft.primary_quote_id)
    if primary is None:
        raise ReviewBlockedError(
            f"quote {draft.primary_quote_id!r} is no longer open/contactable"
        )
    return states, primary


def _inside_cooldown(
    contact_at: datetime,
    *,
    now: datetime,
    cooldown_hours: Decimal,
) -> bool:
    contact_at = _aware_utc(contact_at, "contact_at")
    elapsed_seconds = Decimal(str((now - contact_at).total_seconds()))
    return elapsed_seconds / Decimal("3600") < cooldown_hours


def _has_current_state_cooldown(
    states: Sequence[QuoteState],
    *,
    phone_key: str,
    now: datetime,
    cooldown_hours: Decimal,
) -> bool:
    return any(
        state.last_outbound_at is not None
        and _phone_key(state.customer_phone) == phone_key
        and _inside_cooldown(
            state.last_outbound_at,
            now=now,
            cooldown_hours=cooldown_hours,
        )
        for state in states
    )


def _has_outbox_reservation(
    connection: Connection,
    *,
    draft_id: UUID,
    phone_key: str,
    now: datetime,
    cooldown_hours: Decimal,
) -> bool:
    rows = connection.execute(
        text(
            """
            SELECT created_at
            FROM outbox
            WHERE draft_id <> :draft_id
              AND regexp_replace(customer_phone, '[^0-9]', '', 'g') = :phone_key
            """
        ),
        {"draft_id": str(draft_id), "phone_key": phone_key},
    ).scalars()
    return any(
        _inside_cooldown(
            created_at,
            now=now,
            cooldown_hours=cooldown_hours,
        )
        for created_at in rows
    )


def approve_draft(
    connection: Connection,
    *,
    draft_id: UUID | str,
    now: datetime,
    policy: Policy,
) -> int:
    """Recheck live safety constraints and reserve one idempotent send."""
    now = _aware_utc(now, "now")
    unlocked_draft = _load_draft(connection, draft_id)
    phone_key = _phone_key(unlocked_draft.customer_phone)
    if phone_key is None:
        raise ReviewBlockedError("draft customer phone is not contactable")

    connection.execute(ADVISORY_PHONE_LOCK_QUERY, {"phone_key": phone_key})
    draft = _load_draft(connection, draft_id, for_update=True)
    existing_outbox_id = connection.execute(
        text("SELECT id FROM outbox WHERE draft_id = :draft_id"),
        {"draft_id": str(draft.id)},
    ).scalar_one_or_none()
    if existing_outbox_id is not None:
        return existing_outbox_id
    if draft.status != "pending":
        raise ReviewBlockedError(
            f"draft {draft.id!s} has already been {draft.status}"
        )

    states, primary = _current_open_draft_states(
        connection,
        draft=draft,
        now=now,
        policy=policy,
    )
    validate_draft(
        draft.body,
        max_characters=policy.drafting.max_characters,
        quote_status=primary.status or "",
        tech_name=primary.tech_name,
        require_tech_name=policy.drafting.require_tech_name,
    )
    if _has_current_state_cooldown(
        states,
        phone_key=phone_key,
        now=now,
        cooldown_hours=policy.cooldown_hours,
    ) or _has_outbox_reservation(
        connection,
        draft_id=draft.id,
        phone_key=phone_key,
        now=now,
        cooldown_hours=policy.cooldown_hours,
    ):
        raise ReviewBlockedError(
            "customer-wide cooldown was consumed after this draft was created"
        )

    outbox_id = enqueue_outbox(
        connection,
        draft_id=str(draft.id),
        body=draft.body,
        channel=draft.channel,
        customer_phone=draft.customer_phone,
        created_at=now,
    )
    connection.execute(
        text(
            """
            UPDATE drafts
            SET status = 'approved', updated_at = :now, reviewed_at = :now
            WHERE id = :draft_id
            """
        ),
        {"draft_id": draft.id, "now": now},
    )
    return outbox_id


def reject_draft(
    connection: Connection,
    *,
    draft_id: UUID | str,
    now: datetime,
) -> PersistedDraft:
    """Persist a human rejection without creating a delivery reservation."""
    now = _aware_utc(now, "now")
    draft = _load_draft(connection, draft_id, for_update=True)
    if draft.status == "approved":
        raise ReviewBlockedError(f"draft {draft.id!s} has already been approved")
    if draft.status == "pending":
        connection.execute(
            text(
                """
                UPDATE drafts
                SET status = 'rejected', updated_at = :now, reviewed_at = :now
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
    body: str,
    now: datetime,
    policy: Policy,
) -> PersistedDraft:
    """Validate and persist human-edited copy for a still-contactable draft."""
    now = _aware_utc(now, "now")
    draft = _load_draft(connection, draft_id, for_update=True)
    if draft.status != "pending":
        raise ReviewBlockedError(
            f"draft {draft.id!s} has already been {draft.status}"
        )
    _, primary = _current_open_draft_states(
        connection,
        draft=draft,
        now=now,
        policy=policy,
    )
    normalized_body = validate_draft(
        body,
        max_characters=policy.drafting.max_characters,
        quote_status=primary.status or "",
        tech_name=primary.tech_name,
        require_tech_name=policy.drafting.require_tech_name,
    )
    connection.execute(
        text(
            """
            UPDATE drafts
            SET body = :body, updated_at = :now
            WHERE id = :draft_id
            """
        ),
        {"draft_id": draft.id, "body": normalized_body, "now": now},
    )
    return _load_draft(connection, draft.id)


ActionPrompt = Callable[..., str]
MessagePrompt = Callable[[PersistedDraft, Console], str]


def _default_message_prompt(draft: PersistedDraft, console: Console) -> str:
    return str(Prompt.ask("Message", default=draft.body, console=console))


def _show_draft(draft: PersistedDraft, *, console: Console) -> None:
    context = Text()
    context.append("Quote: ", style="bold")
    context.append(draft.primary_quote_id)
    context.append("\nPhone: ", style="bold")
    context.append(draft.customer_phone)
    console.print(context)
    console.print(Panel(Text(draft.body), title="Draft message", expand=True))


def _show_candidate_queue(
    candidates: Sequence[Candidate],
    *,
    console: Console,
) -> None:
    table = Table(title="Pending follow-ups", box=None, expand=True, padding=(0, 1))
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Customer", min_width=12, no_wrap=True)
    table.add_column("Quote", overflow="fold")
    table.add_column("Reason", overflow="fold")
    table.add_column("Score", justify="right", no_wrap=True)
    for rank, candidate in enumerate(candidates, start=1):
        customer_name = str(getattr(candidate, "customer_name", "") or "").strip()
        customer = "\n".join(
            value for value in (customer_name, candidate.customer_phone) if value
        )
        reason = candidate.reason.replace("_", " ").strip().capitalize()
        table.add_row(
            str(rank),
            customer,
            candidate.primary_quote_id,
            reason,
            f"{candidate.score:.1f}",
        )
    console.print(table)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def run_review(
    engine: Engine,
    *,
    now: datetime | None = None,
    clock: Callable[[], datetime] = _utc_now,
    policy: Policy,
    adapter: LiteLLMAdapter,
    console: Console = DEFAULT_CONSOLE,
    action_prompt: ActionPrompt = prompt_choice,
    message_prompt: MessagePrompt = _default_message_prompt,
) -> ReviewSummary:
    """Run interactive review with short transactions around every state change."""
    fixed_now = _aware_utc(now, "now") if now is not None else None

    def current_time() -> datetime:
        return fixed_now or _aware_utc(clock(), "clock result")

    with engine.begin() as connection:
        candidates = _rank_candidates(load_latest_candidates(connection))
    _show_candidate_queue(candidates, console=console)

    presented = approved = rejected = skipped = blocked = 0
    for candidate in candidates:
        with engine.begin() as connection:
            draft = draft_candidate(
                connection,
                candidate=candidate,
                now=current_time(),
                policy=policy,
                adapter=adapter,
            )
        if draft is None:
            continue

        presented += 1
        _show_draft(draft, console=console)
        while True:
            action = action_prompt(
                escape("Action: [A]pprove, [R]eject, [E]dit, [S]kip"),
                choices=("a", "r", "e", "s"),
                default="s",
                console=console,
                case_sensitive=False,
                show_choices=False,
                show_default=False,
            )
            if action == "s":
                skipped += 1
                break
            if action == "r":
                with engine.begin() as connection:
                    reject_draft(
                        connection,
                        draft_id=draft.id,
                        now=current_time(),
                    )
                rejected += 1
                break
            if action == "a":
                try:
                    with engine.begin() as connection:
                        approve_draft(
                            connection,
                            draft_id=draft.id,
                            now=current_time(),
                            policy=policy,
                        )
                except ReviewBlockedError as exc:
                    console.print(f"[red]Blocked:[/red] {exc}")
                    blocked += 1
                else:
                    approved += 1
                break

            edited_body = message_prompt(draft, console)
            try:
                with engine.begin() as connection:
                    draft = update_draft_message(
                        connection,
                        draft_id=draft.id,
                        body=edited_body,
                        now=current_time(),
                        policy=policy,
                    )
            except (ReviewBlockedError, ValueError) as exc:
                console.print(f"[red]Invalid message:[/red] {exc}")
            else:
                _show_draft(draft, console=console)

    return ReviewSummary(
        presented=presented,
        approved=approved,
        rejected=rejected,
        skipped=skipped,
        blocked=blocked,
    )


def _parse_now(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--now must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--now must include a UTC offset")
    return parsed.astimezone(UTC)


def main(
    argv: Sequence[str] | None = None,
    *,
    adapter: LiteLLMAdapter | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="respawned review")
    parser.add_argument("--now", type=_parse_now)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    args = parser.parse_args(argv)

    policy = load_policy(args.policy)
    active_adapter = adapter or LiteLLMAdapter.from_env()
    engine = get_engine()
    create_tables(engine)
    try:
        summary = run_review(
            engine,
            now=args.now,
            policy=policy,
            adapter=active_adapter,
        )
    finally:
        engine.dispose()

    print(
        "Reviewed {presented} drafts: {approved} approved, {rejected} rejected, "
        "{skipped} skipped, {blocked} blocked.".format(
            presented=summary.presented,
            approved=summary.approved,
            rejected=summary.rejected,
            skipped=summary.skipped,
            blocked=summary.blocked,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
