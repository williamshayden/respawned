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


def _show_source_context(draft: dict, console: Console) -> bool:
    current = draft.get("review_context")
    if not isinstance(current, dict):
        return False
    if current.get("id") != draft["primary_opportunity_id"]:
        raise APIError("The draft's current source context does not match its record. Reopen the draft before reviewing.")
    context = Text("Current saved source context", style="bold")
    context.append(f"\n{current.get('title', current['id'])}")
    context.append(f"\nKind: {current.get('kind', '')} | Status: {current.get('status', '')}")
    if current.get("stage"):
        context.append(f" | Stage: {current['stage']}")
    contact = current.get("contact")
    if contact:
        context.append(f"\nCurrent contact: {contact.get('name') or ''} ({contact['channel']}: {contact['address']})")
    for field in current.get("fields", ()):
        context.append(f"\n{field['label']}: {field['value']}")
    reason = current.get("reason") or {}
    context.append(f"\nReason: {reason.get('label', '')}. {reason.get('detail', '')}")
    if current.get("source_url"):
        context.append(f"\nSource: {current['source_url']}")
    related = current.get("referenced_record_ids") or []
    if related:
        context.append("\nRelated records: " + ", ".join(related))
    context.append("\nSource freshness: unknown.")
    console.print(context)
    if current.get("activities"):
        evidence = Table(title="Current recorded activity", box=None, padding=(0, 1))
        evidence.add_column("When", no_wrap=True)
        evidence.add_column("Activity")
        evidence.add_column("Evidence", overflow="fold")
        for activity in current["activities"]:
            details = activity.get("summary") or ""
            if activity.get("source_url"):
                details += ("\n" if details else "") + activity["source_url"]
            label = f"{activity.get('label') or activity.get('type', '')} ({activity.get('classification', 'unknown')})"
            evidence.add_row(Text(str(activity["occurred_at"])), Text(label), Text(details))
        console.print(evidence)
    source_status = draft.get("source_context_status", "unknown")
    if source_status == "changed":
        console.print("Source details changed since this draft was saved. Review the copy against the current details.")
    elif source_status == "unknown":
        console.print("The saved copy's source context is unknown. Review it against the current details.")
    return True


def _show_draft(draft: dict, console: Console) -> bool:
    current_context = _show_source_context(draft, console)
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
    if not current_context:
        console.print("Read-only draft: this engine did not provide current review context. Upgrade the engine to Respawned 2.3 or later before reviewing with this client.")
    return current_context


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
    if not _show_draft(draft, console):
        return "blocked", False
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
                client.edit_draft(draft["id"], body, draft["review_token"])
            except APIError as exc:
                console.print(Text.assemble(("Not saved: ", "red"), str(exc)))
                if exc.status_code == 422 and not exc.ambiguous:
                    continue
                console.print("Reopen this draft to inspect its current version.")
                return "blocked", False
            try:
                refreshed = client.get_draft(draft["id"])
                if refreshed["id"] != draft["id"] or refreshed["primary_opportunity_id"] != draft["primary_opportunity_id"]:
                    raise APIError("The refreshed draft does not match this record.")
            except APIError as exc:
                console.print(Text.assemble(("Edit saved. Current review could not be loaded: ", "yellow"), str(exc)))
                console.print("Reopen this draft before making another decision.")
                return "blocked", False
            draft = refreshed
            if draft["status"] != "pending":
                console.print(f"Edit saved. This draft is now {draft['status']}; reopen it to inspect the current state.")
                return "blocked", False
            if not _show_draft(draft, console):
                return "blocked", False
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
