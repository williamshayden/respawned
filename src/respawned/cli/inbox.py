"""Inspect unanswered replies without drafting, approving, or sending messages."""

import argparse
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path

from respawned.cli.common import DEFAULT_POLICY_PATH, parse_now
from respawned.core.inbox import list_reply_inbox
from respawned.core.policy import load_policy
from respawned.db.helpers.pg_connect import get_engine


def _limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--limit must be between 1 and 200") from exc
    if not 1 <= parsed <= 200:
        raise argparse.ArgumentTypeError("--limit must be between 1 and 200")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned inbox")
    parser.add_argument("--now", type=parse_now, help="Activity cutoff with UTC offset; source freshness remains unknown")
    parser.add_argument("--limit", type=_limit, default=50, help="Maximum contact routes, 1 to 200 (default: 50)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print structured evidence for agent clients")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    args = parser.parse_args(argv)

    policy = load_policy(args.policy)
    engine = get_engine()
    try:
        with engine.connect() as connection:
            result = list_reply_inbox(
                connection, now=args.now or datetime.now(UTC),
                policy=policy, limit=args.limit,
            )
    finally:
        engine.dispose()

    if args.json_output:
        print(json.dumps(asdict(result), default=lambda value: value.isoformat(), indent=2))
        return 0
    print(f"Reply needed: {len(result.items)} of {result.total} contact routes.")
    print(f"Activity cutoff: {result.as_of.isoformat()}; source freshness unknown.")
    print("Outreach eligibility is not evaluated; cooldown and review can still block action.")
    for item in result.items:
        print(f"\n{item.contact_name or item.contact_key} | {item.channel}: {item.contact_address}")
        print(f"  Contact: {item.contact_key}; latest reply: {item.latest_reply_at.isoformat()}")
        for evidence in item.reply_evidence:
            print(f"  {evidence.opportunity_id} | {evidence.activity_id} | {evidence.occurred_at.isoformat()}")
        if item.pending_outbox_count:
            print(f"  Pending outbox reservations: {item.pending_outbox_count} (not sent).")
    if result.has_more:
        print("\nMore replies exist; increase --limit to show up to 200 contact routes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
