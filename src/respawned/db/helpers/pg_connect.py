"""Postgres connection and schema initialization."""

import os
from pathlib import Path

from sqlalchemy import URL, create_engine
from sqlalchemy.exc import OperationalError

DEFAULT_SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


class DatabaseConnectionError(RuntimeError):
    """The configured application database could not be reached."""


def build_engine_url() -> URL:
    """Build a safely escaped SQLAlchemy URL from host-facing settings."""

    return URL.create(
        "postgresql+psycopg2",
        username=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME"),
    )


def get_engine():
    """Create a SQLAlchemy Engine for Postgres."""
    engine = create_engine(
        build_engine_url(),
        connect_args={"connect_timeout": 5},
        pool_timeout=5,
        pool_pre_ping=True,
    )
    try:
        with engine.connect():
            pass
        return engine
    except OperationalError as exc:
        engine.dispose()
        raise DatabaseConnectionError("Could not connect to Postgres") from exc


def create_tables(engine):
    """Create tables/indexes from schema.sql if they don't already exist."""
    with DEFAULT_SCHEMA_PATH.open() as f:
        schema_sql = f.read()
    with engine.begin() as conn:
        conn.exec_driver_sql(schema_sql)
