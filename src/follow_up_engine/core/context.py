"""Shared business configuration for calendar-based application rules."""

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class BusinessContext:
    """Validated business-local context used across core calculations."""

    timezone_name: str = "UTC"

    def __post_init__(self) -> None:
        try:
            timezone = ZoneInfo(self.timezone_name)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError(
                f"Invalid IANA business timezone: {self.timezone_name!r}"
            ) from exc

        object.__setattr__(self, "timezone_name", timezone.key)
