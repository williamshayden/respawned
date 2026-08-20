"""Store approved drafts in, and export, the delivery outbox."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.engine import Connection

from follow_up_engine.db.helpers.pg_connect import get_engine


CSV_FIELDS = (
    "id",
    "draft_id",
    "body",
    "channel",
    "status",
    "created_at",
    "sent_at",
    "customer_phone",
)


def enqueue_outbox(
    connection: Connection,
    *,
    draft_id: str,
    body: str,
    channel: str,
    customer_phone: str,
    created_at: datetime | None = None,
) -> int:
    """Insert a pending draft once and return its stable outbox row ID."""
    values = {
        "draft_id": draft_id,
        "body": body,
        "channel": channel,
        "customer_phone": customer_phone,
    }
    columns = "draft_id, body, channel, customer_phone"
    placeholders = ":draft_id, :body, :channel, :customer_phone"
    if created_at is not None:
        columns += ", created_at"
        placeholders += ", :created_at"
        values["created_at"] = created_at

    inserted_id = connection.execute(
        text(
            f"""
            INSERT INTO outbox ({columns})
            VALUES ({placeholders})
            ON CONFLICT (draft_id) DO NOTHING
            RETURNING id
            """
        ),
        values,
    ).scalar_one_or_none()
    if inserted_id is not None:
        return inserted_id

    return connection.execute(
        text("SELECT id FROM outbox WHERE draft_id = :draft_id"),
        {"draft_id": draft_id},
    ).scalar_one()


def export_outbox(connection: Connection, path: str | Path) -> int:
    """Write every outbox row to a deterministic CSV ordered by stable ID."""
    rows = connection.execute(
        text(
            f"""
            SELECT {', '.join(CSV_FIELDS)}
            FROM outbox
            ORDER BY id
            """
        )
    ).all()

    destination = Path(path)
    with destination.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(CSV_FIELDS)
        writer.writerows(rows)

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
