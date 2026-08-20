from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from follow_up_engine.core.context import BusinessContext
from follow_up_engine.core.reduce import reduce_quotes


NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _state_for_quote(
    connection,
    quote_id: str,
    *,
    business_context: BusinessContext | None = None,
):
    if business_context is None:
        states = reduce_quotes(connection, now=NOW)
    else:
        states = reduce_quotes(
            connection,
            now=NOW,
            business_context=business_context,
        )

    return next(
        state
        for state in states
        if state.quote_id == quote_id
    )


def test_loaded_duplicate_event_id_produces_expected_view_state(postgres_connection):
    state = _state_for_quote(postgres_connection, "Q-1015")

    assert state.quote_id == "Q-1015"
    assert state.status == "open"
    assert state.amount == Decimal("3800.00")
    assert state.customer_name == "Angela Ortiz"
    assert state.customer_phone == "+19175552015"
    assert state.tech_name == "Brad"
    assert state.created_at == datetime(2026, 8, 8, 11, tzinfo=UTC)
    assert state.quote_sent_at == datetime(2026, 8, 8, 11, tzinfo=UTC)
    assert state.last_viewed_at == datetime(2026, 8, 10, 17, tzinfo=UTC)
    assert state.view_days == 1
    assert state.last_replied_at == datetime(2026, 8, 11, 19, tzinfo=UTC)
    assert state.last_outbound_at is None


def test_duplicate_event_id_uses_deterministic_view_event(postgres_connection):
    postgres_connection.execute(text("ALTER TABLE events DROP CONSTRAINT events_pkey"))
    postgres_connection.execute(
        text(
            """
            INSERT INTO events (event_id, type, quote_id, "timestamp")
            VALUES
                ('00000000-0000-0000-0000-000000000004', 'quote_viewed', 'Q-1003', '2026-08-18T23:30:00Z'),
                ('00000000-0000-0000-0000-000000000004', 'quote_viewed', 'Q-1003', '2026-08-19T00:01:00Z')
            """
        )
    )

    state = _state_for_quote(postgres_connection, "Q-1003")

    assert state.last_viewed_at == datetime(2026, 8, 18, 23, 30, tzinfo=UTC)
    assert state.view_days == 1


def test_view_days_counts_distinct_utc_dates(postgres_connection):
    postgres_connection.execute(
        text(
            """
            INSERT INTO events (event_id, type, quote_id, "timestamp")
            VALUES
                ('00000000-0000-0000-0000-000000000001', 'quote_viewed', 'Q-1003', '2026-08-18T23:30:00Z'),
                ('00000000-0000-0000-0000-000000000002', 'quote_viewed', 'Q-1003', '2026-08-18T23:59:00Z'),
                ('00000000-0000-0000-0000-000000000003', 'quote_viewed', 'Q-1003', '2026-08-19T00:01:00Z')
            """
        )
    )

    state = _state_for_quote(postgres_connection, "Q-1003")

    assert state.view_days == 2


def test_view_days_uses_configured_business_timezone(postgres_connection):
    postgres_connection.execute(
        text(
            """
            INSERT INTO events (event_id, type, quote_id, "timestamp")
            VALUES
                ('00000000-0000-0000-0000-000000000005', 'quote_viewed', 'Q-1003', '2026-08-18T23:30:00Z'),
                ('00000000-0000-0000-0000-000000000006', 'quote_viewed', 'Q-1003', '2026-08-19T00:01:00Z')
            """
        )
    )

    default_utc = _state_for_quote(postgres_connection, "Q-1003")
    explicit_utc = _state_for_quote(
        postgres_connection,
        "Q-1003",
        business_context=BusinessContext("UTC"),
    )
    chicago = _state_for_quote(
        postgres_connection,
        "Q-1003",
        business_context=BusinessContext("America/Chicago"),
    )
    reset_to_utc = _state_for_quote(
        postgres_connection,
        "Q-1003",
        business_context=BusinessContext("UTC"),
    )

    assert default_utc.view_days == 2
    assert explicit_utc.view_days == 2
    assert chicago.view_days == 1
    assert reset_to_utc.view_days == 2


def test_last_outbound_falls_back_to_seed_contact(postgres_connection):
    state = _state_for_quote(postgres_connection, "Q-1003")

    assert state.last_outbound_at == datetime(2026, 8, 1, 19, tzinfo=UTC)


def test_last_outbound_uses_later_seed_contact(postgres_connection):
    state = _state_for_quote(postgres_connection, "Q-1020")

    assert state.last_outbound_at == datetime(2026, 8, 13, 17, tzinfo=UTC)


def test_last_outbound_uses_latest_qualifying_stream_event(postgres_connection):
    postgres_connection.execute(
        text(
            """
            INSERT INTO events
                (event_id, type, quote_id, "timestamp", channel, direction)
            VALUES
                ('00000000-0000-0000-0000-000000000011',
                 'message_sent', 'Q-1003', '2026-08-17T20:00:00Z',
                 'sms', 'outbound'),
                ('00000000-0000-0000-0000-000000000012',
                 'message_sent', 'Q-1003', '2026-08-18T20:00:00Z',
                 'sms', 'outbound'),
                ('00000000-0000-0000-0000-000000000013',
                 'message_sent', 'Q-1003', '2026-08-19T20:00:00Z',
                 'sms', 'inbound'),
                ('00000000-0000-0000-0000-000000000014',
                 'quote_sent', 'Q-1003', '2026-08-20T20:00:00Z',
                 'sms', 'outbound')
            """
        )
    )

    state = _state_for_quote(postgres_connection, "Q-1003")

    assert state.last_outbound_at == datetime(2026, 8, 18, 20, tzinfo=UTC)
