"""Command-line entry point for candidate synchronization."""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from respawned.cli.common import DEFAULT_POLICY_PATH, parse_now
from respawned.core.policy import load_policy
from respawned.core.sync import sync_candidates
from respawned.db.helpers.pg_connect import create_tables, get_engine


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned sync")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--now", type=parse_now)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    args = parser.parse_args(argv)

    engine = get_engine()
    create_tables(engine)
    try:
        with engine.begin() as connection:
            result = sync_candidates(
                connection,
                now=args.now or datetime.now(UTC),
                policy=load_policy(args.policy),
                dry_run=args.dry_run,
                limit=args.limit,
            )
    finally:
        engine.dispose()

    action = "would persist" if result.dry_run else "persisted"
    print(
        f"Selected {len(result.candidates)} candidates; "
        f"{action} {result.inserted_count} new candidates."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
