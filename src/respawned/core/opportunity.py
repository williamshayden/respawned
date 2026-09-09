"""Small, shared facts derived from canonical opportunity state."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from respawned.core.domain import OpportunityState
from respawned.core.time import aware_utc, local_calendar_days_since


def is_open_opportunity(state: OpportunityState) -> bool:
    return isinstance(state.status, str) and state.status.strip().lower() == "open"


def opportunity_origin(state: OpportunityState) -> datetime | None:
    return state.created_at


def is_contactable_opportunity(
    state: OpportunityState,
    now: datetime,
    timezone: ZoneInfo,
    dead_after_days: int,
) -> bool:
    if not is_open_opportunity(state):
        return False
    origin = opportunity_origin(state)
    if origin is None:
        return True
    origin = aware_utc(origin, f"{state.opportunity_id}.created_at")
    return origin <= now and local_calendar_days_since(origin, now, timezone) < dead_after_days


def positive_value(state: OpportunityState) -> Decimal | None:
    if state.kind == "job_application":
        return None
    value = state.value
    return value if value is not None and value.is_finite() and value > 0 else None
