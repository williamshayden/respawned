"""Shared business configuration for calendar-based application rules."""

from dataclasses import dataclass
from importlib.resources import files
from zoneinfo import ZoneInfo


_TZDATA_ROOT = files("tzdata.zoneinfo")
_PACKAGED_IANA_TIMEZONES = frozenset(
    files("tzdata").joinpath("zones").read_text(encoding="utf-8").splitlines()
)


@dataclass(frozen=True, slots=True)
class BusinessContext:
    """Validated business-local context used across core calculations."""

    timezone_name: str = "UTC"

    def __post_init__(self) -> None:
        if self.timezone_name not in _PACKAGED_IANA_TIMEZONES:
            raise ValueError(
                f"Invalid IANA business timezone: {self.timezone_name!r}"
            )

        try:
            zone_path = _TZDATA_ROOT.joinpath(*self.timezone_name.split("/"))
            with zone_path.open("rb") as zone_file:
                timezone = ZoneInfo.from_file(zone_file, key=self.timezone_name)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Invalid IANA business timezone: {self.timezone_name!r}"
            ) from exc

        object.__setattr__(self, "timezone_name", timezone.key)
