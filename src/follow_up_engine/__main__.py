"""Command router for the follow-up engine."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CommandHandler = Callable[[argparse.Namespace], Any]
CommandConfigurer = Callable[[argparse.ArgumentParser], None]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_DIR = PROJECT_ROOT / "seed"


@dataclass(frozen=True)
class CommandSpec:
    """A command's parser metadata, kept separate from its lazy handler."""

    name: str
    help: str
    configure: CommandConfigurer | None = None


def _configure_load(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--seed-dir",
        type=Path,
        default=DEFAULT_SEED_DIR,
        help="Directory containing quotes.json and events.jsonl",
    )


def _configure_outbox(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--path",
        type=Path,
        required=True,
        help="CSV output path",
    )


def _configure_sync(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--now")
    parser.add_argument("--policy", type=Path)


COMMANDS = (
    CommandSpec("load", "Load configured source data into Postgres", _configure_load),
    CommandSpec("sync", "Refresh the prioritized follow-up candidates", _configure_sync),
    CommandSpec("outbox", "Export the delivery outbox to CSV", _configure_outbox),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="follow-up-engine",
        description="Automated quote follow-up engine",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    for command in COMMANDS:
        command_parser = subparsers.add_parser(
            command.name,
            help=command.help,
            description=command.help,
        )
        if command.configure is not None:
            command.configure(command_parser)
    return parser


def _load(args: argparse.Namespace) -> None:
    from follow_up_engine.cli.load_data import load_data

    seed_dir = getattr(args, "seed_dir", DEFAULT_SEED_DIR)
    quotes_filename = os.getenv("QUOTES_FILENAME", "quotes.json")
    events_filename = os.getenv("EVENTS_FILENAME", "events.jsonl")

    load_data(
        quotes_source={"type": "json", "path": str(seed_dir / quotes_filename)},
        events_source={"type": "json", "path": str(seed_dir / events_filename)},
    )


def _outbox(args: argparse.Namespace) -> None:
    from follow_up_engine.cli.outbox import main as outbox_main

    outbox_main(["--path", os.fspath(args.path)])


def _sync(args: argparse.Namespace) -> int:
    from follow_up_engine.cli.sync import main as sync_main

    forwarded: list[str] = []
    if args.dry_run:
        forwarded.append("--dry-run")
    if args.limit is not None:
        forwarded.extend(("--limit", str(args.limit)))
    if args.now is not None:
        forwarded.extend(("--now", args.now))
    if args.policy is not None:
        forwarded.extend(("--policy", os.fspath(args.policy)))
    return sync_main(forwarded)


DEFAULT_HANDLERS: Mapping[str, CommandHandler] = {
    "load": _load,
    "sync": _sync,
    "outbox": _outbox,
}


def main(
    argv: Sequence[str] | None = None,
    *,
    handlers: Mapping[str, CommandHandler] | None = None,
) -> Any:
    """Parse ``argv`` and dispatch a command; no command retains load behavior."""
    args = build_parser().parse_args(argv)
    command = args.command or "load"
    active_handlers = handlers if handlers is not None else DEFAULT_HANDLERS
    return active_handlers[command](args)


if __name__ == "__main__":
    main()
