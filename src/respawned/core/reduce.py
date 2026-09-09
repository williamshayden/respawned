"""Project source activities into canonical opportunity state."""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.core.domain import Activity, OpportunityState
from respawned.core.time import aware_utc


OPPORTUNITY_STATES_QUERY = text(
    """
    SELECT
        opportunity_id,
        status,
        value,
        contact_key,
        contact_name,
        contact_phone,
        contact_email,
        owner_name,
        created_at,
        last_viewed_at,
        last_replied_at,
        last_outbound_at,
        view_timestamps,
        preferred_channel,
        activities,
        kind,
        title,
        context
    FROM opportunity_states
    WHERE CAST(:contact_key AS TEXT) IS NULL OR contact_key = :contact_key
    ORDER BY opportunity_id
    """
)

SET_AS_OF_QUERY = text(
    """
    SELECT set_config('respawned.as_of', :as_of, true)
    """
)


def _activity(values: dict[str, Any]) -> Activity:
    occurred_at = values["occurred_at"]
    if isinstance(occurred_at, str):
        occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    return Activity(
        activity_id=values["activity_id"],
        activity_type=values["activity_type"],
        occurred_at=occurred_at,
        channel=values.get("channel"),
        direction=values.get("direction"),
        summary=values.get("summary"),
        source_url=values.get("source_url"),
        classification=values.get("classification", "unknown"),
    )


def reduce_opportunities(
    conn: Connection,
    now: datetime,
    *,
    contact_key: str | None = None,
) -> list[OpportunityState]:
    """Return deterministic opportunity state using activities visible at ``now``."""
    as_of = aware_utc(now, "reduce_opportunities.now")
    conn.execute(SET_AS_OF_QUERY, {"as_of": as_of.isoformat()})

    states: list[OpportunityState] = []
    for row in conn.execute(
        OPPORTUNITY_STATES_QUERY, {"contact_key": contact_key}
    ).mappings():
        values = dict(row)
        values["view_timestamps"] = tuple(values["view_timestamps"] or ())
        values["activities"] = tuple(
            _activity(item) for item in (values["activities"] or ())
        )
        states.append(OpportunityState(**values))
    return states
