"""Orchestrator: connect, ensure schema, then load each table from its own configured source.

Dispatches per table by source["type"] — both branches currently go
through pg_load.py (Postgres-specific), since json_load.py is purely
a file-format reader with no database knowledge.
"""

import os
import sys
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from follow_up_engine.db.helpers.pg_connect import get_engine, create_tables
from follow_up_engine.db.helpers.pg_load import load_json_file, load_from_database


def load_table(engine, table_name, source):
    source_type = source["type"]

    if source_type == "json":
        return load_json_file(engine, table_name, source["path"])
    elif source_type == "database":
        return load_from_database(engine, table_name, source)
    else:
        sys.exit(f"Unknown source type: {source_type!r} (table: {table_name})")


def load_data(quotes_source, events_source):
    engine = get_engine()
    create_tables(engine)

    try:
        n_quotes = load_table(engine, "quotes", quotes_source)
        print(f"Loaded {n_quotes} quotes from {quotes_source}")

        n_events = load_table(engine, "events", events_source)
        print(f"Loaded {n_events} events from {events_source}")
    except IntegrityError as exc:
        sys.exit(f"Load failed due to a data integrity error (e.g. bad foreign key or constraint): {exc}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    seed_dir = Path(os.getenv("SEED_DIR", "seed"))
    events_filename = os.getenv("EVENTS_FILENAME", "events.jsonl")
    quotes_filename = os.getenv("QUOTES_FILENAME", "quotes.json")

    load_data(
        quotes_source={"type": "json", "path": str(seed_dir / quotes_filename)},
        events_source={"type": "json", "path": str(seed_dir / events_filename)},
    )