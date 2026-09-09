from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from respawned.core.ingest import ingest_records
from respawned.core.reduce import reduce_opportunities
from respawned.db.helpers.pg_connect import DEFAULT_SCHEMA_PATH


def test_existing_schema_migration_keeps_records_view_order_and_legacy_replays(postgres_connection):
    connection = postgres_connection
    # This generated schema is rolled back by the owning test transaction.
    schema = "tracking_migration_" + uuid4().hex
    connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
    connection.exec_driver_sql("""
        CREATE TABLE opportunities (
            id TEXT PRIMARY KEY,
            contact_key TEXT NOT NULL CHECK (btrim(contact_key) <> ''),
            contact_name TEXT, contact_phone TEXT, contact_email TEXT,
            owner_name TEXT, value NUMERIC(12, 2),
            status TEXT NOT NULL DEFAULT 'open',
            created_at TIMESTAMPTZ NOT NULL, last_contact_at TIMESTAMPTZ,
            preferred_channel TEXT,
            CHECK (NULLIF(btrim(contact_phone), '') IS NOT NULL
                OR NULLIF(btrim(contact_email), '') IS NOT NULL)
        );
        CREATE TABLE activities (
            id TEXT PRIMARY KEY, type TEXT NOT NULL,
            opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
            occurred_at TIMESTAMPTZ NOT NULL, channel TEXT, direction TEXT
        );
        INSERT INTO opportunities (id, contact_key, contact_email, created_at)
            VALUES ('legacy', 'contact:legacy', 'person@example.com', '2026-09-01T12:00:00Z');
        INSERT INTO activities (id, type, opportunity_id, occurred_at)
            VALUES ('legacy-reply', 'contact_replied', 'legacy', '2026-09-02T12:00:00Z');
        CREATE VIEW opportunity_states AS SELECT
            id AS opportunity_id, status, value, contact_key, contact_name,
            contact_phone, contact_email, owner_name, created_at,
            NULL::TIMESTAMPTZ AS last_viewed_at,
            NULL::TIMESTAMPTZ AS last_replied_at,
            NULL::TIMESTAMPTZ AS last_outbound_at,
            ARRAY[]::TIMESTAMPTZ[] AS view_timestamps,
            preferred_channel,
            '[]'::JSONB AS activities,
            NULL::TEXT AS last_activity_channel
        FROM opportunities;
    """)
    previous_columns = list(connection.execute(text("SELECT * FROM opportunity_states")).keys())
    schema_sql = DEFAULT_SCHEMA_PATH.read_text()
    connection.exec_driver_sql(schema_sql)
    connection.exec_driver_sql(schema_sql)
    columns = list(connection.execute(text("SELECT * FROM opportunity_states")).keys())
    assert columns == previous_columns + ["kind", "title", "context"]

    result = ingest_records(connection, activities=[{
        "id": "legacy-reply", "type": "contact_replied", "opportunity_id": "legacy",
        "occurred_at": datetime(2026, 9, 2, 12, tzinfo=UTC),
    }], opportunities=[{
        "id": "tracking-only", "kind": "job_application", "status": "open",
        "created_at": datetime(2026, 9, 3, 12, tzinfo=UTC),
        "context": {"company": "Example"},
    }])
    assert result.activities_inserted == 0
    states = {state.opportunity_id: state for state in reduce_opportunities(
        connection, datetime(2026, 9, 9, 12, tzinfo=UTC)
    )}
    assert states["legacy"].kind == "generic"
    assert states["legacy"].last_replied_at == datetime(2026, 9, 2, 12, tzinfo=UTC)
    assert states["tracking-only"].contact_key is None
    assert states["tracking-only"].context.company == "Example"
