"""Shared time calculations for policy and workflow rules."""

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo


def aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def elapsed_hours(value: datetime, now: datetime) -> Decimal:
    seconds = Decimal(str((now - value).total_seconds()))
    return max(Decimal("0"), seconds / Decimal("3600"))


def within_cooldown(
    contact_at: datetime, now: datetime, cooldown_hours: Decimal
) -> bool:
    contact_at = aware_utc(contact_at, "contact_at")
    now = aware_utc(now, "now")
    return contact_at <= now and elapsed_hours(contact_at, now) < cooldown_hours


def local_calendar_days_since(
    value: datetime, now: datetime, timezone: ZoneInfo
) -> int:
    return max(
        0,
        (now.astimezone(timezone).date() - value.astimezone(timezone).date()).days,
    )
