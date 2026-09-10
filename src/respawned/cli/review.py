"""Interactive human review through the engine's HTTP API."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from respawned.client import APIError, RespawnedClient
from respawned.cli.http import client_from_args, configure_connection, run
from respawned.cli.ui import DEFAULT_CONSOLE, prompt_choice


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    presented: int = 0
    approved: int = 0
    rejected: int = 0
    skipped: int = 0
    blocked: int = 0


def _default_message_prompt(draft: dict, console: Console) -> str:
    return str(Prompt.ask("Message", default=draft["body"], console=console))


def _show_draft(draft: dict, console: Console) -> None:
    context = Text()
    context.append("Record: ", style="bold")
    context.append(draft["primary_opportunity_id"])
    if draft.get("contact_name"):
        context.append("\nContact: ", style="bold")
        context.append(draft["contact_name"])
    context.append(f"\n{draft['channel'].upper()}: ", style="bold")
    context.append(draft["contact_address"])
    console.print(context)
    console.print(Panel(Text(draft["body"]), title="Draft message", expand=True))


def _show_queue(candidates: list[dict], console: Console) -> None:
    table = Table(title="Pending follow-ups", box=None, expand=True, padding=(0, 1))
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Contact", min_width=12)
    table.add_column("Record", overflow="fold")
    table.add_column("Reason", overflow="fold")
    for rank, candidate in enumerate(candidates, 1):
        table.add_row(
            str(rank), Text(candidate.get("contact_name") or candidate["contact_address"]),
            Text(candidate["primary_opportunity_id"]),
            Text(candidate["reason"].replace("_", " ").capitalize()),
        )
    console.print(table)


def run_review(
    client: RespawnedClient, *, limit: int = 10, console: Console = DEFAULT_CONSOLE,
    action_prompt: Callable[..., str] = prompt_choice,
    message_prompt: Callable[[dict, Console], str] = _default_message_prompt,
) -> ReviewSummary:
    """Refresh a bounded queue, then present each exact draft for human action."""
    client.sync(limit=limit)
    candidates = client.queue()["items"]
    _show_queue(candidates, console)
    presented = approved = rejected = skipped = blocked = 0
    for candidate in candidates:
        try:
            draft = client.draft_candidate(candidate["id"])
        except APIError as exc:
            console.print(Text.assemble(("Blocked: ", "red"), str(exc)))
            if exc.ambiguous:
                console.print("The draft result is unknown. Reopen it before trying again.")
            blocked += 1
            continue
        if draft["status"] != "pending":
            continue
        presented += 1
        _show_draft(draft, console)
        while True:
            try:
                action = action_prompt(
                    escape("Action: [A]pprove, [R]eject, [E]dit, [S]kip"),
                    choices=("a", "r", "e", "s"), default="s", console=console,
                    case_sensitive=False, show_choices=False, show_default=False,
                )
            except (EOFError, KeyboardInterrupt):
                return ReviewSummary(presented, approved, rejected, skipped + 1, blocked)
            if action == "s":
                skipped += 1
                break
            if action == "e":
                try:
                    body = message_prompt(draft, console)
                except (EOFError, KeyboardInterrupt):
                    return ReviewSummary(presented, approved, rejected, skipped + 1, blocked)
                try:
                    updated = client.edit_draft(draft["id"], body, draft["review_token"])
                    # Version/body come from the response; the approved recipient
                    # context is unchanged. The server still checks every action.
                    draft = dict(draft, **updated)
                except APIError as exc:
                    console.print(Text.assemble(("Not saved: ", "red"), str(exc)))
                    if exc.status_code == 422 and not exc.ambiguous:
                        continue
                    console.print("Reopen this draft to inspect its current version.")
                    blocked += 1
                    break
                _show_draft(draft, console)
                continue
            try:
                if action == "a":
                    client.approve_draft(draft["id"], draft["review_token"])
                    approved += 1
                    console.print("Approved to outbox. Unsent.")
                else:
                    client.reject_draft(draft["id"], draft["review_token"])
                    rejected += 1
            except APIError as exc:
                console.print(Text.assemble(("Not completed: ", "red"), str(exc)))
                console.print("Reopen this draft to inspect its current state.")
                blocked += 1
            break
    return ReviewSummary(presented, approved, rejected, skipped, blocked)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned review", description="Refresh the queue and review drafts interactively")
    parser.add_argument("--limit", type=int, default=10, help="Maximum candidates, 1..200 (default: 10)")
    configure_connection(parser)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")

    def review():
        summary = run_review(client_from_args(args), limit=args.limit)
        print(f"Reviewed {summary.presented} drafts: {summary.approved} approved, "
              f"{summary.rejected} rejected, {summary.skipped} skipped, {summary.blocked} blocked.")
        return 1 if summary.blocked else 0

    return run(review)


if __name__ == "__main__":
    raise SystemExit(main())