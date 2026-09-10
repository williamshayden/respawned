"""Submit supplied copy or request a draft from the engine's model."""
import argparse
from collections.abc import Sequence
from respawned.cli.http import client_from_args, configure_connection, print_json, read_text, run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned draft", description=__doc__)
    parser.add_argument("record_id", help="Stable record ID")
    parser.add_argument("--body-file", help="Use exact UTF-8 draft text from this file, or - for standard input")
    configure_connection(parser)
    args = parser.parse_args(argv)

    def draft():
        body = read_text(args.body_file, limit=40000) if args.body_file is not None else None
        print_json(client_from_args(args).draft(args.record_id, body=body))

    return run(draft)


if __name__ == "__main__":
    raise SystemExit(main())