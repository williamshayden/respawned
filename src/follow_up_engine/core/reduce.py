"""Typed access to the database-backed quote-state reducer."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from follow_up_engine.core.context import BusinessContext


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

SET_BUSINESS_TIMEZONE_QUERY = text(
    """
    SELECT set_config(
        'follow_up_engine.business_timezone',
        :timezone_name,
        true
    )
    """
)


def reduce_quotes(
    conn: Connection,
    now: datetime,
    *,
    business_context: BusinessContext | None = None,
) -> list[QuoteState]:
    """Return the current deterministic state for every quote."""
    _ = now
    context = business_context or BusinessContext()
    conn.execute(
        SET_BUSINESS_TIMEZONE_QUERY,
        {"timezone_name": context.timezone_name},
    )
    rows = conn.execute(QUOTE_STATES_QUERY).mappings()
    return [QuoteState(**row) for row in rows]
