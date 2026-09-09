"""Export delivery reservations from the generic outbox."""

import argparse
import csv
import json
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.db.helpers.pg_connect import get_engine

CSV_FIELDS = (
    "id",
    "draft_id",
    "contact_key",
    "contact_address",
    "contact_name",
    "channel",
    "opportunity_ids",
    "body",
    "status",
    "authorization_mode",
    "created_at",
    "sent_at",
)


def export_outbox(connection: Connection, path: str | Path) -> int:
    """Write every outbox row to a deterministic CSV ordered by stable ID."""
    rows = (
        connection.execute(
            text(f"SELECT {', '.join(CSV_FIELDS)} FROM outbox ORDER BY id")
        )
        .mappings()
        .all()
    )
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            values = dict(row)
            for field in ("created_at", "sent_at"):
                if values[field] is not None:
                    values[field] = values[field].astimezone(UTC)
            values["opportunity_ids"] = json.dumps(
                values["opportunity_ids"], separators=(",", ":")
            )
            writer.writerow(values)
    return len(rows)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export the delivery outbox to CSV")
    parser.add_argument("--path", type=Path, required=True, help="CSV output path")
    args = parser.parse_args(argv)

    engine = get_engine()
    try:
        with engine.connect() as connection:
            exported_count = export_outbox(connection, args.path)
    finally:
        engine.dispose()
    print(f"Exported {exported_count} outbox rows to {args.path}")


if __name__ == "__main__":
    main()
