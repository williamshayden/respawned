import importlib
from contextlib import contextmanager

import pytest
from sqlalchemy.exc import OperationalError


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def exec_driver_sql(self, statement):
        self.statements.append(statement)


class RecordingEngine:
    def __init__(self):
        self.connection = RecordingConnection()

    @contextmanager
    def begin(self):
        yield self.connection


def test_create_tables_uses_packaged_schema_despite_container_environment(
    monkeypatch,
):
    from respawned.db.helpers import pg_connect

    monkeypatch.setenv("SCHEMA_PATH", "/src/respawned/db/schema.sql")
    reloaded_pg_connect = importlib.reload(pg_connect)
    engine = RecordingEngine()

    try:
        reloaded_pg_connect.create_tables(engine)
    finally:
        monkeypatch.delenv("SCHEMA_PATH")
        importlib.reload(pg_connect)

    assert len(engine.connection.statements) == 1
    assert "CREATE TABLE IF NOT EXISTS opportunities" in engine.connection.statements[0]


def test_build_engine_url_escapes_credentials(monkeypatch):
    from respawned.db.helpers.pg_connect import build_engine_url

    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "respawned")
    monkeypatch.setenv("DB_USER", "engine-user")
    monkeypatch.setenv("DB_PASSWORD", "p@ss/word")

    url = build_engine_url()

    assert url.username == "engine-user"
    assert url.password == "p@ss/word"
    assert url.render_as_string(hide_password=False).startswith(
        "postgresql+psycopg2://engine-user:p%40ss%2Fword@"
    )


def test_get_engine_raises_library_error_instead_of_exiting(monkeypatch):
    from respawned.db.helpers import pg_connect

    class FailingConnection:
        def __enter__(self):
            raise OperationalError("connect", {}, RuntimeError("offline"))

        def __exit__(self, *_args):
            return False

    class FailingEngine:
        disposed = False

        def connect(self):
            return FailingConnection()

        def dispose(self):
            self.disposed = True

    engine = FailingEngine()
    monkeypatch.setattr(pg_connect, "create_engine", lambda _url, **_options: engine)

    with pytest.raises(pg_connect.DatabaseConnectionError) as error:
        pg_connect.get_engine()

    assert str(error.value) == "Could not connect to Postgres"
    assert engine.disposed is True
