from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from follow_up_engine.core.reduce import reduce_quotes


NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _state_for_quote(connection, quote_id: str):
    return next(
        state
        for state in reduce_quotes(connection, now=NOW)
        if state.quote_id == quote_id
    )


def test_duplicate_event_id_does_not_inflate_view_days(postgres_connection):
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
