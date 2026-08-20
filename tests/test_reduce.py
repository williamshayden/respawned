from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from follow_up_engine.core.context import BusinessContext
from follow_up_engine.core.reduce import QuoteState, reduce_quotes


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


def test_channel_uses_latest_non_null_event_evidence(postgres_connection):
    quote_id = "Q-CHANNEL-LATEST"
    postgres_connection.execute(
        text(
            """
            INSERT INTO quotes (id, customer_name, customer_phone, status)
            VALUES (:quote_id, 'Channel Customer', '+13125550199', 'open')
            """
        ),
        {"quote_id": quote_id},
    )
    postgres_connection.execute(
        text(
            """
            INSERT INTO events (
                event_id, type, quote_id, "timestamp", channel
            ) VALUES
                ('00000000-0000-0000-0000-000000000091',
                 'quote_sent', :quote_id, '2026-08-18T10:00:00Z', 'sms'),
                ('00000000-0000-0000-0000-000000000092',
                 'customer_replied', :quote_id, '2026-08-19T10:00:00Z', 'email'),
                ('00000000-0000-0000-0000-000000000093',
                 'quote_viewed', :quote_id, '2026-08-20T10:00:00Z', NULL)
            """
        ),
        {"quote_id": quote_id},
    )

    state = _state_for_quote(postgres_connection, quote_id)

    assert state.channel == "email"


def test_quote_accepted_overrides_open_seed_status(postgres_connection):
    postgres_connection.execute(
        text(
            """
            INSERT INTO quotes (
                id,
                customer_name,
                customer_phone,
                tech_name,
                amount,
                status,
                created_at
            )
            VALUES (
                'Q-T3-ACCEPT',
                'Task Three Accept',
                '+15550000031',
                'Test Tech',
                123.00,
                'open',
                '2026-08-14T10:00:00Z'
            )
            """
        )
    )
    postgres_connection.execute(
        text(
            """
            INSERT INTO events (event_id, type, quote_id, "timestamp")
            VALUES (
                '00000000-0000-0000-0000-000000000031',
                'quote_accepted',
                'Q-T3-ACCEPT',
                '2026-08-15T10:00:00Z'
            )
            """
        )
    )

    state = _state_for_quote(
        postgres_connection,
        'Q-T3-ACCEPT',
        business_context=BusinessContext("UTC"),
    )

    assert state.status == "accepted"


def test_dismissed_seed_status_is_preserved_without_acceptance(postgres_connection):
    postgres_connection.execute(
        text(
            """
            INSERT INTO quotes (
                id,
                customer_name,
                customer_phone,
                tech_name,
                amount,
                status,
                created_at
            )
            VALUES (
                'Q-T3-DISMISSED',
                'Task Three Dismissed',
                '+15550000032',
                'Test Tech',
                234.00,
                'dismissed',
                '2026-08-14T10:00:00Z'
            )
            """
        )
    )

    state = _state_for_quote(
        postgres_connection,
        'Q-T3-DISMISSED',
        business_context=BusinessContext("UTC"),
    )

    assert state.status == "dismissed"


def test_reversed_event_insertion_order_produces_identical_state(
    postgres_connection,
):
    quote_id = "Q-T3-ORDER"
    postgres_connection.execute(
        text(
            """
            INSERT INTO quotes (
                id,
                customer_name,
                customer_phone,
                tech_name,
                amount,
                status,
                created_at,
                last_contact_at
            )
            VALUES (
                :quote_id,
                'Task Three Order',
                '+15550000033',
                'Test Tech',
                321.00,
                'open',
                '2026-08-14T09:00:00Z',
                '2026-08-18T15:00:00Z'
            )
            """
        ),
        {"quote_id": quote_id},
    )

    events = [
        {
            "event_id": "00000000-0000-0000-0000-000000000041",
            "type": "quote_sent",
            "timestamp": datetime(2026, 8, 16, 12, tzinfo=UTC),
            "channel": None,
            "direction": None,
        },
        {
            "event_id": "00000000-0000-0000-0000-000000000042",
            "type": "quote_sent",
            "timestamp": datetime(2026, 8, 15, 12, tzinfo=UTC),
            "channel": None,
            "direction": None,
        },
        {
            "event_id": "00000000-0000-0000-0000-000000000043",
            "type": "quote_viewed",
            "timestamp": datetime(2026, 8, 18, 23, 30, tzinfo=UTC),
            "channel": None,
            "direction": None,
        },
        {
            "event_id": "00000000-0000-0000-0000-000000000044",
            "type": "quote_viewed",
            "timestamp": datetime(2026, 8, 19, 0, 30, tzinfo=UTC),
            "channel": None,
            "direction": None,
        },
        {
            "event_id": "00000000-0000-0000-0000-000000000045",
            "type": "customer_replied",
            "timestamp": datetime(2026, 8, 17, 10, tzinfo=UTC),
            "channel": "sms",
            "direction": "inbound",
        },
        {
            "event_id": "00000000-0000-0000-0000-000000000046",
            "type": "customer_replied",
            "timestamp": datetime(2026, 8, 18, 10, tzinfo=UTC),
            "channel": "sms",
            "direction": "inbound",
        },
        {
            "event_id": "00000000-0000-0000-0000-000000000047",
            "type": "message_sent",
            "timestamp": datetime(2026, 8, 18, 14, tzinfo=UTC),
            "channel": "sms",
            "direction": "outbound",
        },
        {
            "event_id": "00000000-0000-0000-0000-000000000048",
            "type": "message_sent",
            "timestamp": datetime(2026, 8, 19, 10, tzinfo=UTC),
            "channel": "sms",
            "direction": "outbound",
        },
    ]
    insert_events = text(
        """
        INSERT INTO events (
            event_id,
            type,
            quote_id,
            "timestamp",
            channel,
            direction
        )
        VALUES (
            :event_id,
            :type,
            :quote_id,
            :timestamp,
            :channel,
            :direction
        )
        """
    )

    def load_events(rows):
        postgres_connection.execute(
            insert_events,
            [{**row, "quote_id": quote_id} for row in rows],
        )

    context = BusinessContext("America/Chicago")
    load_events(events)
    state_forward = _state_for_quote(
        postgres_connection,
        quote_id,
        business_context=context,
    )

    postgres_connection.execute(
        text("DELETE FROM events WHERE quote_id = :quote_id"),
        {"quote_id": quote_id},
    )
    load_events(reversed(events))
    state_reverse = _state_for_quote(
        postgres_connection,
        quote_id,
        business_context=context,
    )

    expected_state = QuoteState(
        quote_id=quote_id,
        status="open",
        amount=Decimal("321.00"),
        customer_name="Task Three Order",
        customer_phone="+15550000033",
        tech_name="Test Tech",
        created_at=datetime(2026, 8, 14, 9, tzinfo=UTC),
        quote_sent_at=datetime(2026, 8, 15, 12, tzinfo=UTC),
        last_viewed_at=datetime(2026, 8, 19, 0, 30, tzinfo=UTC),
        view_days=1,
        last_replied_at=datetime(2026, 8, 18, 10, tzinfo=UTC),
        last_outbound_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
        view_timestamps=(
            datetime(2026, 8, 18, 23, 30, tzinfo=UTC),
            datetime(2026, 8, 19, 0, 30, tzinfo=UTC),
        ),
        channel="sms",
    )

    assert state_forward == expected_state
    assert state_reverse == expected_state
    assert state_reverse == state_forward
