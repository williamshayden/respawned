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


def _review_draft(
    client: RespawnedClient, draft: dict, *, console: Console,
    action_prompt: Callable[..., str], message_prompt: Callable[[dict, Console], str],
) -> tuple[str, bool]:
    """Return the human decision and whether input ended the review session."""
    _show_draft(draft, console)
    while True:
        try:
            action = action_prompt(
                escape("Action: [A]pprove, [R]eject, [E]dit, [S]kip"),
                choices=("a", "r", "e", "s"), default="s", console=console,
                case_sensitive=False, show_choices=False, show_default=False,
            )
        except (EOFError, KeyboardInterrupt):
            return "skipped", True
        if action == "s":
            return "skipped", False
        if action == "e":
            try:
                body = message_prompt(draft, console)
            except (EOFError, KeyboardInterrupt):
                return "skipped", True
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
                return "blocked", False
            _show_draft(draft, console)
            continue
        try:
            if action == "a":
                client.approve_draft(draft["id"], draft["review_token"])
                console.print("Approved to outbox. Unsent.")
                return "approved", False
            client.reject_draft(draft["id"], draft["review_token"])
            return "rejected", False
        except APIError as exc:
            console.print(Text.assemble(("Not completed: ", "red"), str(exc)))
            console.print("Reopen this draft to inspect its current state.")
            return "blocked", False


def run_record_review(
    client: RespawnedClient, record_id: str, *, console: Console = DEFAULT_CONSOLE,
    action_prompt: Callable[..., str] = prompt_choice,
    message_prompt: Callable[[dict, Console], str] = _default_message_prompt,
) -> ReviewSummary:
    """Read one saved draft and review its exact persisted recipient and copy."""
    record = client.get_record(record_id)
    saved = record.get("draft")
    if saved is None:
        console.print(Text.assemble(("No saved draft for record: ", "yellow"), record_id))
        console.print("Create a draft for this record with respawned draft and --body-file, then review it again.")
        return ReviewSummary(blocked=1)
    draft = client.get_draft(saved["id"])
    if draft["id"] != saved["id"] or draft["primary_opportunity_id"] != record_id:
        raise APIError("The saved draft does not match this record. Reopen the record before reviewing.")
    if draft["status"] != "pending":
        message = f"This draft is already {draft['status']}."
        if draft.get("outbox_id") is not None:
            message += f" Outbox item: {draft['outbox_id']}."
        console.print(message)
        return ReviewSummary()
    action, _interrupted = _review_draft(client, draft, console=console,
                                        action_prompt=action_prompt, message_prompt=message_prompt)
    return ReviewSummary(presented=1, **{action: 1})


def run_review(
    client: RespawnedClient, *, limit: int = 10, console: Console = DEFAULT_CONSOLE,
    action_prompt: Callable[..., str] = prompt_choice,
    message_prompt: Callable[[dict, Console], str] = _default_message_prompt,
) -> ReviewSummary:
    """Refresh a bounded queue, then present each exact draft for human action."""
    client.sync(limit=limit)
    candidates = client.queue()["items"]
    _show_queue(candidates, console)
    presented = 0
    counts = dict(approved=0, rejected=0, skipped=0, blocked=0)
    for candidate in candidates:
        try:
            draft = client.draft_candidate(candidate["id"])
        except APIError as exc:
            console.print(Text.assemble(("Blocked: ", "red"), str(exc)))
            if exc.ambiguous:
                console.print("The draft result is unknown. Reopen it before trying again.")
            counts["blocked"] += 1
            continue
        if draft["status"] != "pending":
            continue
        presented += 1
        action, interrupted = _review_draft(client, draft, console=console,
                                            action_prompt=action_prompt, message_prompt=message_prompt)
        counts[action] += 1
        if interrupted:
            break
    return ReviewSummary(presented=presented, **counts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned review", description="Review one saved draft or refresh the queue for human review")
    parser.add_argument("record_id", nargs="?", help="Review this record's saved draft without generating text")
    parser.add_argument("--limit", type=int, help="Maximum queue candidates, 1..200 (default: 10); omit with a record ID")
    configure_connection(parser)
    args = parser.parse_args(argv)
    if args.record_id is not None and args.limit is not None:
        parser.error("--limit applies to queue review; omit it when reviewing a record ID")
    if args.limit is not None and not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")

    def review():
        client = client_from_args(args)
        summary = (run_record_review(client, args.record_id) if args.record_id is not None
                   else run_review(client, limit=args.limit if args.limit is not None else 10))
        print(f"Reviewed {summary.presented} drafts: {summary.approved} approved, "
              f"{summary.rejected} rejected, {summary.skipped} skipped, {summary.blocked} blocked.")
        return 1 if summary.blocked else 0

    return run(review)


if __name__ == "__main__":
    raise SystemExit(main())
