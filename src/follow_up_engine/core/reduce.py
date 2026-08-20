"""Typed access to the database-backed quote-state reducer."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True, slots=True)
class QuoteState:
    quote_id: str
    status: str | None
    amount: Decimal | None
    customer_name: str
    customer_phone: str | None
    tech_name: str | None
    created_at: datetime | None
    quote_sent_at: datetime | None
    last_viewed_at: datetime | None
    view_days: int
    last_replied_at: datetime | None
    last_outbound_at: datetime | None


QUOTE_STATES_QUERY = text(
    """
    SELECT
        quote_id,
        status,
        amount,
        customer_name,
        customer_phone,
        tech_name,
        created_at,
        quote_sent_at,
        last_viewed_at,
        view_days,
        last_replied_at,
        last_outbound_at
    FROM quote_states
    ORDER BY quote_id
    """
)


def reduce_quotes(conn: Connection, now: datetime) -> list[QuoteState]:
    """Return the current deterministic state for every quote."""
    _ = now
    rows = conn.execute(QUOTE_STATES_QUERY).mappings()
    return [QuoteState(**row) for row in rows]
