"""CLI clients for the engine API, plus local engine lifecycle commands."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import version
import os
from pathlib import Path
from typing import Any

from respawned.cli.http import client_from_args, configure_connection, print_json, run

DEFAULT_SEED_DIR = Path(__file__).with_name("demo_data")
CLIENT_COMMANDS = {"import", "demo", "sync", "draft", "review", "inbox", "outbox", "process"}


@dataclass(frozen=True)
class CommandSpec:
    name: str
    help: str


COMMANDS = (
    CommandSpec("init", "Initialize this engine's database"),
    CommandSpec("serve", "Start an engine"),
    CommandSpec("ui", "Start a local engine and open its UI"),
    CommandSpec("import", "Import records through the engine API"),
    CommandSpec("demo", "Import bundled sample records"),
    CommandSpec("sync", "Refresh or preview the engine's queue"),
    CommandSpec("draft", "Submit draft text or request model-generated copy"),
    CommandSpec("review", "Refresh the queue and review drafts interactively"),
    CommandSpec("inbox", "Read unanswered replies"),
    CommandSpec("outbox", "Read approved messages or export outbox history"),
    CommandSpec("process", "Process a batch under the engine's review policy"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="respawned", description="Follow-up workflows through one engine API")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('respawned')}")
    configure_connection(parser)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    for command in COMMANDS:
        child = commands.add_parser(command.name, help=command.help, description=command.help)
        if command.name in CLIENT_COMMANDS:
            configure_connection(child, defaults=False)
        if command.name == "serve":
            child.add_argument("--host", default="127.0.0.1")
            child.add_argument("--port", type=int, default=os.getenv("APP_PORT", "8000"))
            child.add_argument("--api-only", action="store_true", help="Omit the bundled UI")
        elif command.name == "ui":
            child.add_argument("--port", type=int, default=os.getenv("APP_PORT", "8000"))
            child.add_argument("--no-open", action="store_true", help="Print the browser link without opening it")
        elif command.name == "demo":
            child.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
        elif command.name == "import":
            child.add_argument("--file", required=True, help="JSON batch file, or - for standard input")
        elif command.name == "draft":
            child.add_argument("record_id", help="Stable record ID")
            child.add_argument("--body-file", help="UTF-8 draft text file, or - for standard input")
        elif command.name in {"sync", "review", "inbox", "process"}:
            child.add_argument("--limit", type=int, default=None)
            if command.name == "sync":
                child.add_argument("--dry-run", action="store_true", help="Preview without saving a queue")
            if command.name == "inbox":
                child.add_argument("--json", action="store_true", dest="json_output")
            if command.name != "process":
                child.add_argument("--now", help=argparse.SUPPRESS)
                child.add_argument("--policy", help=argparse.SUPPRESS)
        elif command.name == "outbox":
            from respawned.cli.outbox import configure_outbox
            configure_outbox(child)
    return parser


def _init(_args) -> int:
    from respawned.db.helpers.pg_connect import create_tables, get_engine
    engine = get_engine()
    try:
        create_tables(engine)
    finally:
        engine.dispose()
    print("Initialized the Respawned database.")
    return 0


def _serve(args) -> int:
    from respawned.cli.browser import launch_ui
    if not 1 <= args.port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    previous_assets = os.environ.get("RESPAWNED_UI_DIST")
    if args.api_only:
        os.environ["RESPAWNED_UI_DIST"] = "off"
    try:
        if args.host in {"127.0.0.1", "localhost"} and not os.environ.get("RESPAWNED_REVIEW_TOKEN", "").strip():
            launch_ui(args.port, open_browser=False, show_ui_link=not args.api_only)
        else:
            if not os.environ.get("RESPAWNED_REVIEW_TOKEN", "").strip():
                raise ValueError("Set RESPAWNED_REVIEW_TOKEN before exposing an engine beyond loopback")
            import uvicorn
            uvicorn.run("respawned.api.app:app", host=args.host, port=args.port)
    finally:
        if args.api_only:
            if previous_assets is None:
                os.environ.pop("RESPAWNED_UI_DIST", None)
            else:
                os.environ["RESPAWNED_UI_DIST"] = previous_assets
    return 0


def _ui(args) -> int:
    from respawned.cli.browser import launch_ui
    launch_ui(args.port, open_browser=not args.no_open)
    return 0


def _http_args(args) -> list[str]:
    forwarded = ["--timeout", str(args.timeout)]
    if args.api_url is not None:
        forwarded += ["--api-url", args.api_url]
    return forwarded


def _client_command(args) -> int:
    if args.command == "demo":
        from respawned.cli.demo import load_demo
        return run(lambda: print_json(load_demo(args.seed_dir, client_from_args(args))))
    module_name = "ingest" if args.command == "import" else args.command
    module = import_module("respawned.cli." + module_name)
    forwarded = _http_args(args)
    if args.command == "import":
        forwarded += ["--file", args.file]
    elif args.command == "draft":
        forwarded += [args.record_id]
        if args.body_file is not None:
            forwarded += ["--body-file", args.body_file]
    elif args.command == "outbox":
        if args.path is not None:
            forwarded += ["--path", os.fspath(args.path)]
        if args.json_output:
            forwarded += ["--json"]
        if args.pending:
            forwarded += ["--pending"]
        if args.limit is not None:
            forwarded += ["--limit", str(args.limit)]
    else:
        if args.limit is not None:
            forwarded += ["--limit", str(args.limit)]
        if args.command == "sync" and args.dry_run:
            forwarded += ["--dry-run"]
        if args.command == "inbox" and args.json_output:
            forwarded += ["--json"]
    return module.main(forwarded)


DEFAULT_HANDLERS: Mapping[str, Callable[[argparse.Namespace], Any]] = {
    "init": _init, "serve": _serve, "ui": _ui,
    **{name: _client_command for name in CLIENT_COMMANDS},
}


def main(argv: Sequence[str] | None = None, *, handlers=None) -> Any:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if getattr(args, "now", None) is not None or getattr(args, "policy", None) is not None:
        parser.error("The engine owns time and policy. Set RESPAWNED_POLICY_PATH on the server; use the simulation harness for a fixed clock.")
    if args.command not in CLIENT_COMMANDS and args.api_url is not None:
        parser.error("--api-url applies to API client commands, not engine lifecycle commands")
    active = handlers if handlers is not None else DEFAULT_HANDLERS
    try:
        return active[args.command](args)
    except (OSError, ValueError) as exc:
        import sys
        print(f"respawned: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())