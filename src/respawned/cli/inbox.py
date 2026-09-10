"""Read unanswered replies from the engine's HTTP API."""
import argparse
from collections.abc import Sequence
from respawned.cli.http import client_from_args, configure_connection, print_json, run


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned inbox", description=__doc__)
    parser.add_argument("--limit", type=int, default=50, help="Maximum contact routes, 1..200")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print the API result as JSON")
    configure_connection(parser)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")

    def inbox():
        result = client_from_args(args).inbox(limit=args.limit)
        if args.json_output:
            print_json(result)
            return
        print(f"Reply needed: {len(result['items'])} of {result['total']} contact routes.")
        for item in result["items"]:
            print(f"\n{item['contact_name'] or item['contact_key']} | {item['channel']}: {item['contact_address']}")
            print(f"  Latest reply: {item['latest_reply_at']}")
            for evidence in item["reply_evidence"]:
                print(f"  {evidence['opportunity_id']} | {evidence['activity_id']} | {evidence['occurred_at']}")
            if item["pending_outbox_count"]:
                print(f"  Approved and unsent: {item['pending_outbox_count']}")
        if result["has_more"]:
            print("\nMore replies exist. Increase --limit to show up to 200 contact routes.")

    return run(inbox)


if __name__ == "__main__":
    raise SystemExit(main())