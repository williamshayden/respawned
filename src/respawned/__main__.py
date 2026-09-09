"""Command router for Respawned."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any


CommandHandler = Callable[[argparse.Namespace], Any]
CommandConfigurer = Callable[[argparse.ArgumentParser], None]
DEFAULT_SEED_DIR = Path(__file__).resolve().parent / "demo_data"


@dataclass(frozen=True)
class CommandSpec:
    """A command's parser metadata, kept separate from its lazy handler."""

    name: str
    help: str
    configure: CommandConfigurer | None = None


def _configure_demo(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--seed-dir",
        type=Path,
        default=DEFAULT_SEED_DIR,
        help="Directory containing quotes.json and events.jsonl (default: bundled demo)",
    )


def _configure_serve(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=os.getenv("APP_PORT", "8000"))
    parser.add_argument("--api-only", action="store_true", help="Serve the workflow API without the bundled browser UI")


def _configure_outbox(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--path",
        type=Path,
        required=True,
        help="CSV output path",
    )


def _configure_ui(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", type=int, default=os.getenv("APP_PORT", "8000"))
    parser.add_argument("--no-open", action="store_true", help="Print the one-use local launch link without opening a browser")


def _configure_sync(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--now")
    parser.add_argument("--policy", type=Path)


def _configure_review(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--now")
    parser.add_argument("--policy", type=Path)


def _configure_inbox(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--now", help="Activity cutoff with UTC offset; source freshness remains unknown")
    parser.add_argument("--limit", type=int, help="Maximum contact routes, 1 to 200 (default: 50)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print structured evidence for agent clients")
    parser.add_argument("--policy", type=Path)


COMMANDS = (
    CommandSpec("init", "Initialize or update the application schema"),
    CommandSpec("demo", "Load the bundled legacy demo data", _configure_demo),
    CommandSpec("serve", "Run the bundled browser UI and local workflow API", _configure_serve),
    CommandSpec("ui", "Open the local browser UI without copying an access token", _configure_ui),
    CommandSpec("sync", "Refresh the prioritized follow-up candidates", _configure_sync),
    CommandSpec("review", "Review the prioritized follow-up candidates", _configure_review),
    CommandSpec("inbox", "Inspect unanswered replies independently of outreach cooldown", _configure_inbox),
    CommandSpec("outbox", "Export the delivery outbox to CSV", _configure_outbox),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="respawned",
        description="Source-neutral follow-up orchestration",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('respawned')}"
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


def _init(_args: argparse.Namespace) -> None:
    from respawned.db.helpers.pg_connect import create_tables, get_engine

    engine = get_engine()
    try:
        create_tables(engine)
    finally:
        engine.dispose()
    print("Initialized the Respawned schema.")


def _demo(args: argparse.Namespace) -> None:
    from respawned.cli.demo import load_demo

    result = load_demo(args.seed_dir)
    print(
        f"Loaded {result.opportunities_upserted} demo opportunities and "
        f"{result.activities_inserted} new activities."
    )


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    previous_assets = os.environ.get("RESPAWNED_UI_DIST")
    if args.api_only:
        os.environ["RESPAWNED_UI_DIST"] = "off"
    try:
        uvicorn.run("respawned.api.app:app", host=args.host, port=args.port)
    finally:
        if args.api_only:
            if previous_assets is None:
                os.environ.pop("RESPAWNED_UI_DIST", None)
            else:
                os.environ["RESPAWNED_UI_DIST"] = previous_assets


def _outbox(args: argparse.Namespace) -> None:
    from respawned.cli.outbox import main as outbox_main

    outbox_main(["--path", os.fspath(args.path)])


def _ui(args: argparse.Namespace) -> None:
    from respawned.cli.browser import launch_ui

    launch_ui(args.port, open_browser=not args.no_open)


def _sync(args: argparse.Namespace) -> int:
    from respawned.cli.sync import main as sync_main

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


def _inbox(args: argparse.Namespace) -> int:
    from respawned.cli.inbox import main as inbox_main

    forwarded: list[str] = []
    if args.now is not None:
        forwarded.extend(("--now", args.now))
    if args.limit is not None:
        forwarded.extend(("--limit", str(args.limit)))
    if args.json_output:
        forwarded.append("--json")
    if args.policy is not None:
        forwarded.extend(("--policy", os.fspath(args.policy)))
    return inbox_main(forwarded)


def _review(args: argparse.Namespace) -> int:
    from respawned.cli.review import main as review_main

    forwarded: list[str] = []
    if args.now is not None:
        forwarded.extend(("--now", args.now))
    if args.policy is not None:
        forwarded.extend(("--policy", os.fspath(args.policy)))
    return review_main(forwarded)


DEFAULT_HANDLERS: Mapping[str, CommandHandler] = {
    "init": _init,
    "demo": _demo,
    "serve": _serve,
    "ui": _ui,
    "sync": _sync,
    "review": _review,
    "inbox": _inbox,
    "outbox": _outbox,
}


def main(
    argv: Sequence[str] | None = None,
    *,
    handlers: Mapping[str, CommandHandler] | None = None,
) -> Any:
    """Parse ``argv`` and dispatch one explicit command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    command = args.command
    active_handlers = handlers if handlers is not None else DEFAULT_HANDLERS
    return active_handlers[command](args)


if __name__ == "__main__":
    raise SystemExit(main())
