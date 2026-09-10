"""Refresh the engine's queue through HTTP."""
import argparse
from collections.abc import Sequence
from respawned.cli.http import client_from_args, configure_connection, run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned sync", description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving a queue")
    parser.add_argument("--limit", type=int, default=10, help="Maximum candidates, 1..200")
    configure_connection(parser)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")

    def sync():
        result = client_from_args(args).sync(limit=args.limit, dry_run=args.dry_run)
        action = "would save" if result["dry_run"] else "saved"
        print(f"Selected {result['candidate_count']} candidates; {action} {result['inserted_count']} new candidates.")

    return run(sync)


if __name__ == "__main__":
    raise SystemExit(main())