"""Postgres connection handling, via SQLAlchemy."""
# Other db_types (mysql, sqlite, etc.) would get their own sibling helper
# file here (e.g. mysql_connect.py) rather than branching inside this one —
# see helpers/pg_load.py for where a future non-Postgres "database" source
# type dispatches from.

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import OperationalError

DEFAULT_SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"
SCHEMA_PATH = Path(os.getenv("SCHEMA_PATH", str(DEFAULT_SCHEMA_PATH)))


def build_engine_url():
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    name = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


def get_engine():
    """Create a SQLAlchemy Engine for Postgres."""
    url = build_engine_url()
    try:
        engine = create_engine(url)
        with engine.connect():
            pass
        return engine
    except OperationalError as exc:
        sys.exit(f"Could not connect to Postgres: {exc}")


def create_tables(engine):
    """Create tables/indexes from schema.sql if they don't already exist."""
    with open(SCHEMA_PATH) as f:
        schema_sql = f.read()
    with engine.begin() as conn:
        conn.exec_driver_sql(schema_sql)


def get_table_columns(engine, table_name):
    """Return column names for a table, via SQLAlchemy's inspector."""
    inspector = inspect(engine)
    return [col["name"] for col in inspector.get_columns(table_name)]


def get_primary_key(engine, table_name):
    """Return the primary key column name for a table."""
    inspector = inspect(engine)
    pk = inspector.get_pk_constraint(table_name)
    return pk["constrained_columns"][0]