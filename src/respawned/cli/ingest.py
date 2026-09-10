"""Import a canonical record batch through the engine API."""
import argparse
from collections.abc import Sequence
from respawned.cli.http import client_from_args, configure_connection, print_json, read_json, run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned import", description=__doc__)
    parser.add_argument("--file", required=True, help="JSON batch file, or - for standard input")
    configure_connection(parser)
    args = parser.parse_args(argv)

    def ingest():
        payload = read_json(args.file)
        print_json(client_from_args(args).import_records(payload))

    return run(ingest)


if __name__ == "__main__":
    raise SystemExit(main())