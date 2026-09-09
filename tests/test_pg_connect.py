import importlib
from contextlib import contextmanager


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
    assert "CREATE TABLE IF NOT EXISTS quotes" in engine.connection.statements[0]
