from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    JSON,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    select,
)
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from respawned.api.app import app, get_connection
from respawned.core import ingest as ingest_module
from respawned.core.contact import lock_contact_keys
from respawned.core.contracts import ActivityIn, OpportunityIn
from respawned.core.ingest import (
    IngestConflictError,
    UnknownOpportunityError,
    ingest_records,
)

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)

metadata = MetaData()
opportunities_table = Table(
    "opportunities",
    metadata,
    Column("id", String, primary_key=True),
    Column("contact_key", String),
    Column("contact_name", String),
    Column("contact_phone", String),
    Column("contact_email", String),
    Column("owner_name", String),
    Column("value", Numeric(12, 2)),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_contact_at", DateTime(timezone=True)),
    Column("preferred_channel", String),
    Column("kind", String, nullable=False, server_default="generic"),
    Column("title", String),
    Column("context", JSON, nullable=False, default=dict),
)
activities_table = Table(
    "activities",
    metadata,
    Column("id", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("opportunity_id", String, ForeignKey("opportunities.id"), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("channel", String),
    Column("direction", String),
    Column("summary", String),
    Column("source_url", String),
    Column("classification", String, nullable=False, server_default="unknown"),
)


def opportunity(**overrides):
    return {
        "id": "opp-1",
        "contact_key": "contact-1",
        "contact_name": "Ada",
        "contact_phone": None,
        "contact_email": "ada@example.com",
        "owner_name": "Grace",
        "value": 100,
        "status": "open",
        "created_at": NOW,
        "last_contact_at": None,
        "preferred_channel": "email",
    } | overrides


def activity(**overrides):
    return {
        "id": "activity-1",
        "type": "opportunity_viewed",
        "opportunity_id": "opp-1",
        "occurred_at": NOW,
        "channel": "email",
        "direction": "inbound",
    } | overrides


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(engine, monkeypatch):
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "ingest-test-operator")
    def override_connection():
        with engine.begin() as connection:
            yield connection

    app.dependency_overrides[get_connection] = override_connection
    try:
        with TestClient(app, headers={"Authorization": "Bearer ingest-test-operator"}) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_ingestion_is_idempotent_and_opportunities_are_mutable(engine):
    with engine.begin() as connection:
        first = ingest_records(
            connection,
            opportunities=[opportunity()],
            activities=[activity()],
        )
        replay = ingest_records(
            connection,
            opportunities=[opportunity(value=250)],
            activities=[activity()],
        )

        stored_opportunity = (
            connection.execute(select(opportunities_table)).mappings().one()
        )
        stored_activities = (
            connection.execute(select(activities_table)).mappings().all()
        )

    assert first.opportunities_upserted == 1
    assert first.activities_inserted == 1
    assert replay.opportunities_upserted == 1
    assert replay.activities_inserted == 0
    assert stored_opportunity["value"] == 250
    assert len(stored_activities) == 1


def test_conflicting_duplicate_activity_is_rejected(engine):
    with engine.begin() as connection:
        ingest_records(
            connection,
            opportunities=[opportunity()],
            activities=[activity()],
        )
        with pytest.raises(IngestConflictError, match="different payload"):
            ingest_records(
                connection,
                activities=[activity(type="customer_replied")],
            )


@pytest.mark.parametrize("missing_field", ("channel", "direction"))
def test_sparse_activity_replay_treats_omitted_optional_as_none(engine, missing_field):
    with engine.begin() as connection:
        ingest_records(
            connection,
            opportunities=[opportunity()],
            activities=[activity()],
        )
        sparse = activity()
        sparse.pop(missing_field)

        with pytest.raises(IngestConflictError, match="different payload"):
            ingest_records(connection, activities=[sparse])


def test_sparse_activity_replay_matches_explicit_nulls(engine):
    sparse = activity(channel=None, direction=None)
    sparse.pop("channel")
    sparse.pop("direction")
    with engine.begin() as connection:
        first = ingest_records(
            connection,
            opportunities=[opportunity()],
            activities=[sparse],
        )
        replay = ingest_records(
            connection,
            activities=[activity(channel=None, direction=None)],
        )

    assert first.activities_inserted == 1
    assert replay.activities_inserted == 0


def test_sparse_opportunities_share_one_canonical_batch_shape(engine):
    sparse = {
        "id": "opp-2",
        "contact_key": "contact-2",
        "contact_email": "second@example.com",
        "status": "open",
        "created_at": NOW,
    }
    with engine.begin() as connection:
        result = ingest_records(
            connection,
            opportunities=[opportunity(), sparse],
        )
        stored = (
            connection.execute(
                select(opportunities_table).where(opportunities_table.c.id == "opp-2")
            )
            .mappings()
            .one()
        )

    assert result.opportunities_upserted == 2
    assert stored["contact_email"] == "second@example.com"
    assert stored["contact_phone"] is None
    assert stored["owner_name"] is None
    assert stored["preferred_channel"] is None


def test_sparse_opportunity_update_clears_omitted_snapshot_fields(engine):
    with engine.begin() as connection:
        ingest_records(connection, opportunities=[opportunity()])
        ingest_records(
            connection,
            opportunities=[
                {
                    "id": "opp-1",
                    "contact_key": "contact-1",
                    "contact_email": "new@example.com",
                    "status": "open",
                    "created_at": NOW,
                }
            ],
        )
        stored = connection.execute(select(opportunities_table)).mappings().one()

    assert stored["contact_email"] == "new@example.com"
    for field in ("contact_name", "owner_name", "value", "preferred_channel"):
        assert stored[field] is None


def test_service_normalizes_source_identity_before_deduplication(engine):
    with engine.begin() as connection:
        result = ingest_records(
            connection,
            opportunities=[
                opportunity(),
                opportunity(id=" opp-1 ", contact_key=" contact-1 "),
            ],
            activities=[activity(opportunity_id=" opp-1 ")],
        )
        stored = connection.execute(select(opportunities_table)).mappings().one()

    assert result.opportunities_upserted == 1
    assert result.activities_inserted == 1
    assert stored["contact_key"] == "contact-1"


@pytest.mark.parametrize(
    ("model", "record"), [(OpportunityIn, opportunity), (ActivityIn, activity)]
)
def test_new_record_insertion_order_is_independent_of_batch_order(model, record):
    rows = [record(id="z-last"), record(id="a-first")]

    forward = ingest_module._rows(rows, kind="record", model=model)
    reverse = ingest_module._rows(reversed(rows), kind="record", model=model)

    assert forward == reverse
    assert [row["id"] for row in forward] == ["a-first", "z-last"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"contact_key": " "},
        {"contact_phone": None, "contact_email": None},
        {"status": "accepted"},
        {"created_at": NOW.replace(tzinfo=None)},
        {"preferred_channel": "fax"},
        {"unexpected": "not part of the contract"},
    ],
)
def test_service_validates_opportunities_before_database_access(overrides):
    with pytest.raises(ValidationError):
        ingest_records(object(), opportunities=[opportunity(**overrides)])


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": " "},
        {"opportunity_id": " "},
        {"occurred_at": NOW.replace(tzinfo=None)},
        {"direction": "unknown"},
        {"channel": "fax"},
        {"unexpected": "not part of the contract"},
    ],
)
def test_service_validates_entire_batch_before_database_access(overrides):
    with pytest.raises(ValidationError):
        ingest_records(
            object(),
            opportunities=[opportunity()],
            activities=[activity(**overrides)],
        )


def test_activity_conflict_is_preflighted_before_opportunity_write(engine):
    with engine.begin() as connection:
        ingest_records(
            connection,
            opportunities=[opportunity(value=100)],
            activities=[activity()],
        )
        with pytest.raises(IngestConflictError, match="different payload"):
            ingest_records(
                connection,
                opportunities=[opportunity(value=999)],
                activities=[activity(type="customer_replied")],
            )
        stored_value = connection.execute(
            select(opportunities_table.c.value)
        ).scalar_one()

    assert stored_value == 100


def test_ingestion_locks_sorted_existing_and_incoming_contact_keys_before_write(
    engine, monkeypatch
):
    with engine.begin() as connection:
        ingest_records(
            connection,
            opportunities=[
                opportunity(id="moving", contact_key="contact:z-old"),
                opportunity(id="activity-target", contact_key="contact:a"),
            ],
        )
        observed = []
        real_lock = ingest_module.lock_contact_keys

        def capture_lock(bind, keys):
            locked = real_lock(bind, keys)
            current_key = bind.execute(
                select(opportunities_table.c.contact_key).where(
                    opportunities_table.c.id == "moving"
                )
            ).scalar_one()
            observed.append((locked, current_key))
            return locked

        monkeypatch.setattr(ingest_module, "lock_contact_keys", capture_lock)
        ingest_records(
            connection,
            opportunities=[opportunity(id="moving", contact_key="contact:m-new")],
            activities=[activity(id="activity-2", opportunity_id="activity-target")],
        )

    assert observed == [
        (("contact:a", "contact:m-new", "contact:z-old"), "contact:z-old")
    ]


def test_postgres_contact_lock_uses_shared_sorted_advisory_query():
    calls = []

    class Connection:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, statement, parameters):
            calls.append((" ".join(str(statement).split()), parameters))

    assert lock_contact_keys(
        Connection(), (" contact:z ", "contact:a", "contact:z", None)
    ) == ("contact:a", "contact:z")
    assert calls == [
        (
            "SELECT pg_advisory_xact_lock(hashtextextended(:contact_key, 0))",
            {"contact_key": "contact:a"},
        ),
        (
            "SELECT pg_advisory_xact_lock(hashtextextended(:contact_key, 0))",
            {"contact_key": "contact:z"},
        ),
    ]


def test_conflicting_ids_in_one_batch_are_rejected_before_writing(engine):
    with engine.begin() as connection:
        with pytest.raises(IngestConflictError, match="conflicting activity payloads"):
            ingest_records(
                connection,
                opportunities=[opportunity()],
                activities=[activity(), activity(type="customer_replied")],
            )
        assert connection.execute(select(opportunities_table)).all() == []


def test_service_accepts_a_sqlalchemy_session(engine):
    with Session(engine) as session:
        result = ingest_records(session, opportunities=[opportunity()])
        session.commit()
    with engine.connect() as connection:
        assert (
            connection.execute(select(opportunities_table.c.id)).scalar_one() == "opp-1"
        )
    assert result.opportunities_upserted == 1


def test_unknown_activity_opportunity_is_rejected_before_writing(engine):
    with engine.begin() as connection:
        with pytest.raises(UnknownOpportunityError, match="missing-opportunity"):
            ingest_records(
                connection,
                opportunities=[opportunity(id="valid-opportunity")],
                activities=[activity(opportunity_id="missing-opportunity")],
            )

        assert connection.execute(select(opportunities_table)).all() == []


def test_opportunity_in_same_batch_satisfies_its_activity(engine):
    with engine.begin() as connection:
        result = ingest_records(
            connection,
            opportunities=[opportunity()],
            activities=[activity()],
        )

    assert result.activities_inserted == 1


@pytest.mark.parametrize("same_payload", [True, False])
def test_concurrent_activity_conflict_is_rechecked(engine, monkeypatch, same_payload):
    incoming = activity() if same_payload else activity(type="customer_replied")
    with engine.begin() as connection:
        ingest_records(
            connection,
            opportunities=[opportunity()],
            activities=[activity()],
        )
        read_existing = ingest_module._existing_activities
        calls = 0

        def activity_appears_after_insert(database, table, ids):
            nonlocal calls
            calls += 1
            return {} if calls == 1 else read_existing(database, table, ids)

        monkeypatch.setattr(
            ingest_module,
            "_existing_activities",
            activity_appears_after_insert,
        )
        if same_payload:
            assert (
                ingest_records(connection, activities=[incoming]).activities_inserted
                == 0
            )
        else:
            with pytest.raises(IngestConflictError, match="concurrently received"):
                ingest_records(connection, activities=[incoming])


def test_http_ingestion_supports_email_and_dependency_override(engine, client):
    assert client.get("/healthz").json() == {"status": "ok"}
    response = client.post(
        "/v1/ingest",
        json={
            "opportunities": [opportunity(created_at=NOW.isoformat(), value="100.00")],
            "activities": [activity(occurred_at=NOW.isoformat())],
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "opportunities_upserted": 1,
        "activities_inserted": 1,
    }
    with engine.connect() as connection:
        stored = connection.execute(select(opportunities_table)).mappings().one()
    assert stored["contact_email"] == "ada@example.com"


def test_http_validation_rejects_an_unreachable_contact(client):
    response = client.post(
        "/v1/ingest",
        json={
            "opportunities": [
                opportunity(
                    contact_phone=None,
                    contact_email=None,
                    preferred_channel=None,
                    created_at=NOW.isoformat(),
                )
            ]
        },
    )
    assert response.status_code == 422


def test_http_reports_conflicting_activity_as_409(client):
    initial = {
        "opportunities": [opportunity(created_at=NOW.isoformat())],
        "activities": [activity(occurred_at=NOW.isoformat())],
    }
    assert client.post("/v1/ingest", json=initial).status_code == 200
    initial["activities"][0]["type"] = "customer_replied"
    assert client.post("/v1/ingest", json=initial).status_code == 409


def test_http_reports_unknown_activity_opportunity_as_retryable_409(client):
    response = client.post(
        "/v1/ingest",
        json={
            "activities": [
                activity(
                    opportunity_id="not-arrived-yet",
                    occurred_at=NOW.isoformat(),
                )
            ]
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "activities reference opportunities not yet ingested: ['not-arrived-yet']"
        )
    }


def test_http_validation_rejects_noncanonical_status(client):
    response = client.post(
        "/v1/ingest",
        json={
            "opportunities": [
                opportunity(status="accepted", created_at=NOW.isoformat())
            ]
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "value", ["-0.01", "10000000000", "9999999999.995", "1.001", "NaN", "Infinity"]
)
def test_http_and_service_reject_values_that_cannot_be_stored_exactly(client, value):
    payload = opportunity(value=value, created_at=NOW.isoformat())

    assert client.post("/v1/ingest", json={"opportunities": [payload]}).status_code == 422
    with pytest.raises(ValidationError):
        ingest_records(object(), opportunities=[payload])


@pytest.mark.parametrize("value", ["0", "1.2300", "9999999999.99"])
def test_http_accepts_exact_numeric_boundary_values(engine, client, value):
    response = client.post(
        "/v1/ingest",
        json={"opportunities": [opportunity(value=value, created_at=NOW.isoformat())]},
    )

    assert response.status_code == 200
    with engine.connect() as connection:
        stored = connection.execute(select(opportunities_table.c.value)).scalar_one()
    assert stored == Decimal(value)


def test_stale_channel_preference_does_not_block_an_available_route(client):
    response = client.post(
        "/v1/ingest",
        json={
            "opportunities": [
                opportunity(
                    preferred_channel="sms",
                    contact_name=None,
                    contact_phone=None,
                    contact_email="ada@example.com",
                    created_at=NOW.isoformat(),
                )
            ]
        },
    )
    assert response.status_code == 200
