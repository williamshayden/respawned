"""Rich ARES interface for the transactional review service."""

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from sqlalchemy.engine import Engine

from respawned.cli.common import DEFAULT_POLICY_PATH, parse_now
from respawned.cli.ui import DEFAULT_CONSOLE, prompt_choice
from respawned.core.candidates import Candidate
from respawned.core.policy import Policy, load_policy
from respawned.core.review import (
    PersistedDraft,
    ReviewBlockedError,
    approve_draft,
    authorize_draft_automatically,
    draft_candidate,
    load_latest_candidates,
    rank_candidates,
    reject_draft,
    update_draft_message,
)
from respawned.core.time import aware_utc
from respawned.db.helpers.pg_connect import create_tables, get_engine
from respawned.llm.adapter import LiteLLMAdapter


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    presented: int = 0
    approved: int = 0
    rejected: int = 0
    skipped: int = 0
    blocked: int = 0
    automatically_authorized: int = 0


ActionPrompt = Callable[..., str]
MessagePrompt = Callable[[PersistedDraft, Console], str]


def _default_message_prompt(draft: PersistedDraft, console: Console) -> str:
    return str(Prompt.ask("Message", default=draft.body, console=console))


def _show_draft(draft: PersistedDraft, *, console: Console) -> None:
    context = Text()
    context.append("Opportunity: ", style="bold")
    context.append(draft.primary_opportunity_id)
    if draft.contact_name:
        context.append("\nContact: ", style="bold")
        context.append(draft.contact_name)
    context.append(f"\n{draft.channel.upper()}: ", style="bold")
    context.append(draft.contact_address)
    console.print(context)
    console.print(Panel(Text(draft.body), title="Draft message", expand=True))


def _show_candidate_queue(candidates: Sequence[Candidate], *, console: Console) -> None:
    table = Table(title="Pending follow-ups", box=None, expand=True, padding=(0, 1))
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Contact", min_width=12, no_wrap=True)
    table.add_column("Opportunity", overflow="fold")
    table.add_column("Reason", overflow="fold")
    table.add_column("Score", justify="right", no_wrap=True)
    for rank, candidate in enumerate(candidates, start=1):
        contact = "\n".join(
            value
            for value in (candidate.contact_name, candidate.contact_address)
            if value
        )
        table.add_row(
            str(rank),
            Text(contact),
            Text(candidate.primary_opportunity_id),
            Text(candidate.reason.replace("_", " ").strip().capitalize()),
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
    """Review or automatically authorize candidates under the operator's policy."""
    fixed_now = aware_utc(now, "now") if now is not None else None

    def current_time() -> datetime:
        return fixed_now or aware_utc(clock(), "clock result")

    with engine.begin() as connection:
        candidates = rank_candidates(load_latest_candidates(connection))
    _show_candidate_queue(candidates, console=console)

    presented = approved = rejected = skipped = blocked = automatically_authorized = 0
    for candidate in candidates:
        try:
            with engine.begin() as connection:
                draft = draft_candidate(
                    connection,
                    candidate=candidate,
                    now=current_time(),
                    policy=policy,
                    adapter=adapter,
                )
        except ReviewBlockedError as exc:
            console.print(Text.assemble(("Blocked: ", "red"), str(exc)))
            blocked += 1
            continue
        if draft is None:
            continue

        presented += 1
        _show_draft(draft, console=console)
        if policy.review.mode == "automatic":
            try:
                with engine.begin() as connection:
                    authorize_draft_automatically(
                        connection,
                        draft_id=draft.id,
                        expected_review_token=draft.review_token,
                        now=current_time(),
                        policy=policy,
                    )
            except ReviewBlockedError as exc:
                console.print(Text.assemble(("Blocked: ", "red"), str(exc)))
                blocked += 1
            else:
                automatically_authorized += 1
                console.print("Automatically authorized under configured policy; queued, not sent.")
            continue
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
                try:
                    with engine.begin() as connection:
                        reject_draft(
                            connection,
                            draft_id=draft.id,
                            expected_review_token=draft.review_token,
                            now=current_time(),
                        )
                except ReviewBlockedError as exc:
                    console.print(Text.assemble(("Blocked: ", "red"), str(exc)))
                    blocked += 1
                else:
                    rejected += 1
                break
            if action == "a":
                try:
                    with engine.begin() as connection:
                        approve_draft(
                            connection,
                            draft_id=draft.id,
                            expected_review_token=draft.review_token,
                            now=current_time(),
                            policy=policy,
                        )
                except ReviewBlockedError as exc:
                    console.print(Text.assemble(("Blocked: ", "red"), str(exc)))
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
                        expected_review_token=draft.review_token,
                        body=edited_body,
                        now=current_time(),
                        policy=policy,
                    )
            except (ReviewBlockedError, ValueError) as exc:
                console.print(Text.assemble(("Invalid message: ", "red"), str(exc)))
            else:
                _show_draft(draft, console=console)

    return ReviewSummary(presented, approved, rejected, skipped, blocked,
                         automatically_authorized)


def main(
    argv: Sequence[str] | None = None,
    *,
    adapter: LiteLLMAdapter | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="respawned review")
    parser.add_argument("--now", type=parse_now)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    args = parser.parse_args(argv)

    policy = load_policy(args.policy)
    engine = get_engine()
    try:
        create_tables(engine)
        summary = run_review(
            engine,
            now=args.now,
            policy=policy,
            adapter=adapter or LiteLLMAdapter.from_env(),
        )
    finally:
        engine.dispose()

    print(
        f"Processed {summary.presented} drafts: {summary.approved} approved, {summary.rejected} rejected, "
        f"{summary.skipped} skipped, {summary.blocked} blocked, "
        f"{summary.automatically_authorized} automatically authorized."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
