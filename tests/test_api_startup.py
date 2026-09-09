"""Concurrent cold starts reuse one engine and serialize shared-schema DDL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from time import monotonic
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from respawned.api import app as api_module
from respawned.db.helpers.pg_connect import create_tables


def test_first_parallel_ui_requests_initialize_one_engine(postgres_engine, monkeypatch):
    schema = "respawned_cold_start_" + uuid4().hex
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engines = []
    factory_entered = Event()
    release_factory = Event()

    def factory():
        engine = create_engine(postgres_engine.url,
                               connect_args={"options": f"-csearch_path={schema}"})
        engines.append(engine)
        factory_entered.set()
        assert release_factory.wait(5)
        return engine

    monkeypatch.setattr(api_module, "get_engine", factory)
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "cold-start-test")
    api_module.get_api_engine.cache_clear()
    paths = ["/v1/ui/records", "/v1/ui/outbox", "/v1/ui/inbox", "/v1/ui/workspaces"]
    start = Barrier(len(paths) + 1)

    try:
        with TestClient(api_module.app, raise_server_exceptions=False) as client:
            def request(path):
                start.wait(5)
                return client.get(path, headers={"Authorization": "Bearer cold-start-test"})

            with ThreadPoolExecutor(max_workers=len(paths)) as executor:
                futures = [executor.submit(request, path) for path in paths]
                start.wait(5)
                assert factory_entered.wait(5)
                # Keep the first cache miss in flight while its peer HTTP
                # requests resolve their database dependencies.
                release_factory.wait(0.2)
                release_factory.set()
                responses = [future.result(timeout=10) for future in futures]
            assert [response.status_code for response in responses] == [200] * len(paths)
            assert len(engines) == 1
            assert api_module.get_api_engine() is engines[0]
            assert api_module.get_api_engine.cache_info().currsize == 1
        assert api_module.get_api_engine.cache_info().currsize == 0
    finally:
        release_factory.set()
        api_module.get_api_engine.cache_clear()
        for engine in engines:
            engine.dispose()
        with postgres_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


def test_schema_initialization_waits_for_other_process_transaction(postgres_engine):
    schema = "respawned_schema_lock_" + uuid4().hex
    application_name = "schema-probe-" + uuid4().hex
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(postgres_engine.url, connect_args={
        "options": f"-csearch_path={schema}", "application_name": application_name,
    })
    blocker = engine.connect()
    transaction = blocker.begin()
    blocker.exec_driver_sql("""
        SELECT pg_advisory_xact_lock(
            hashtext('respawned:schema-initialization'), hashtext(current_schema())
        )
    """)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(create_tables, engine)
            deadline = monotonic() + 5
            waiting = False
            try:
                while monotonic() < deadline and not future.done():
                    with postgres_engine.connect() as observer:
                        waiting = bool(observer.execute(text("""
                            SELECT EXISTS (
                                SELECT 1 FROM pg_stat_activity
                                WHERE application_name = :application_name
                                  AND wait_event_type = 'Lock' AND wait_event = 'advisory'
                            )
                        """), {"application_name": application_name}).scalar_one())
                    if waiting:
                        break
                assert waiting, "schema initialization did not wait for the other process lock"
                assert blocker.execute(text("SELECT to_regclass(:table_name)"),
                                       {"table_name": f"{schema}.opportunities"}).scalar_one() is None
            finally:
                transaction.rollback()
            future.result(timeout=10)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT to_regclass('opportunities')")).scalar_one() == "opportunities"
    finally:
        if transaction.is_active:
            transaction.rollback()
        blocker.close()
        engine.dispose()
        with postgres_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
