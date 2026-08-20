"""Postgres-specific data loading.

Owns everything Postgres-specific about loading records into a table:
column/PK introspection (via pg_connect)
"""

import sys

from sqlalchemy import MetaData, Table
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .pg_connect import get_table_columns, get_primary_key
from .json_load import read_json_records


def _build_upsert_statement(table, pk, update_cols):
    stmt = pg_insert(table)
    update_dict = {c: stmt.excluded[c] for c in update_cols}
    return stmt.on_conflict_do_update(index_elements=[pk], set_=update_dict)


def upsert_records(engine, table_name, records):
    if not records:
        return 0

    # TODO: Fail fast when one input batch reuses a primary key with
    # conflicting payload fields; exact duplicate records remain idempotent.
    columns = get_table_columns(engine, table_name)
    pk = get_primary_key(engine, table_name)
    cols = [c for c in columns if any(c in r for r in records)]
    update_cols = [c for c in cols if c != pk]

    rows = [{c: r.get(c) for c in cols} for r in records]

    metadata = MetaData()
    table = Table(table_name, metadata, autoload_with=engine)
    statement = _build_upsert_statement(table, pk, update_cols)

    with engine.begin() as conn:
        conn.execute(statement, rows)
    return len(rows)


def load_json_file(engine, table_name, path):
    """Read records from a JSON/JSONL file and upsert them into table_name."""
    records = read_json_records(path)
    return upsert_records(engine, table_name, records)


def load_from_database(engine, table_name, source):
    """
    Load records into table_name from another existing database.

    source: {"type": "database", "db_type": "postgres", ...connection info...}

    Not yet implemented — plug in a second SQLAlchemy engine (built from
    source's connection info), SELECT the source table, and upsert into
    the target engine/table here.
    """
    sys.exit(f"source type 'database' not yet implemented (table: {table_name}, source: {source})")
