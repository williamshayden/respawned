"""Read approved messages or export outbox history through HTTP."""
from __future__ import annotations
import argparse
from collections.abc import Sequence
from pathlib import Path
from respawned.cli.http import client_from_args, configure_connection, print_json, run, write_output


def configure_outbox(parser: argparse.ArgumentParser) -> None:
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--path", type=Path, help="Write complete outbox history as CSV")
    output.add_argument("--json", action="store_true", dest="json_output", help="Print complete outbox history as JSON")
    parser.add_argument("--pending", action="store_true", help="With --json, read approved pending messages")
    parser.add_argument("--limit", type=int, help="Pending batch size, 1..200 (default: 50)")


def validate_outbox(parser: argparse.ArgumentParser, args) -> None:
    if args.pending and not args.json_output:
        parser.error("--pending requires --json")
    if args.limit is not None:
        if not args.pending or not args.json_output:
            parser.error("--limit requires --pending --json")
        if not 1 <= args.limit <= 200:
            parser.error("--limit must be between 1 and 200")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned outbox", description=__doc__)
    configure_outbox(parser)
    configure_connection(parser)
    args = parser.parse_args(argv)
    validate_outbox(parser, args)

    def outbox():
        client = client_from_args(args)
        if args.pending:
            print_json(client.pending_outbox(limit=args.limit or 50))
        elif args.json_output:
            result = client.export_outbox(format="json")
            print_json({"items": result["items"], "has_more": False})
        else:
            csv_text = client.export_outbox(format="csv")
            write_output(args.path, csv_text)
            print(f"Exported outbox history to {args.path}")

    return run(outbox)


if __name__ == "__main__":
    raise SystemExit(main())