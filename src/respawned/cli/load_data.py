"""Load configured quote and event sources into Postgres."""

import sys

from sqlalchemy.exc import IntegrityError

from respawned.db.helpers.pg_connect import create_tables, get_engine
from respawned.db.helpers.pg_load import load_from_database, load_json_file


def load_table(engine, table_name, source):
    source_type = source["type"]

    if source_type == "json":
        return load_json_file(engine, table_name, source["path"])
    if source_type == "database":
        return load_from_database(engine, table_name, source)
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
        sys.exit(
            "Load failed due to a data integrity error "
            f"(e.g. bad foreign key or constraint): {exc}"
        )
    finally:
        engine.dispose()
