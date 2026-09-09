"""Export delivery reservations from the generic outbox."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.engine import Connection

from respawned.core.outbox import list_outbox_rows, render_outbox_csv
from respawned.db.helpers.pg_connect import get_engine


def export_outbox(connection: Connection, path: str | Path) -> int:
    """Write every outbox row to the shared, spreadsheet-safe CSV format."""
    rows = list_outbox_rows(connection)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as output:
        output.write(render_outbox_csv(rows))
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
