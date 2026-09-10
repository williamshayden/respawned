"""Process a bounded batch under the engine's configured review policy."""
import argparse
from collections.abc import Sequence
from respawned.cli.http import client_from_args, configure_connection, print_json, run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned process", description=__doc__)
    parser.add_argument("--limit", type=int, default=10, help="Maximum candidates, 1..50")
    configure_connection(parser)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 50:
        parser.error("--limit must be between 1 and 50")

    def process():
        result = client_from_args(args).process(limit=args.limit)
        print_json(result)
        return 1 if any(item["status"] == "blocked" for item in result["items"]) else 0

    return run(process)


if __name__ == "__main__":
    raise SystemExit(main())