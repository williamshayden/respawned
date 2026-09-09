"""Source-neutral domain types used by ingestion and follow-up workflows."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


SUPPORTED_CHANNELS = frozenset({"email", "sms"})


def _channel(value: str) -> str:
    channel = value.strip().lower()
    if channel not in SUPPORTED_CHANNELS:
        supported = ", ".join(sorted(SUPPORTED_CHANNELS))
        raise ValueError(f"unsupported contact channel {value!r}; expected {supported}")
    return channel


def _address(value: str | None) -> str | None:
    if value is None:
        return None
    address = value.strip()
    return address or None


@dataclass(frozen=True, slots=True)
class ContactPoint:
    """A validated destination for one supported delivery channel."""

    channel: str
    address: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", _channel(self.channel))
        address = _address(self.address)
        if address is None:
            raise ValueError("contact address must not be empty")
        object.__setattr__(self, "address", address)


@dataclass(frozen=True, slots=True)
class Activity:
    """One source activity associated with an opportunity."""

    activity_id: str
    activity_type: str
    occurred_at: datetime
    channel: str | None = None
    direction: str | None = None


@dataclass(frozen=True, slots=True)
class OpportunityState:
    """Canonical state required by the Respawned."""

    opportunity_id: str
    status: str | None = None
    value: Decimal | None = None
    contact_key: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    owner_name: str | None = None
    created_at: datetime | None = None
    last_viewed_at: datetime | None = None
    last_replied_at: datetime | None = None
    last_outbound_at: datetime | None = None
    view_timestamps: tuple[datetime, ...] = ()
    preferred_channel: str | None = None
    activities: tuple[Activity, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "view_timestamps", tuple(self.view_timestamps))
        object.__setattr__(self, "activities", tuple(self.activities))
        if self.preferred_channel is not None:
            object.__setattr__(
                self,
                "preferred_channel",
                _channel(self.preferred_channel),
            )


def resolve_contact(state: OpportunityState) -> ContactPoint | None:
    """Choose a usable destination, preferring SMS when none is specified."""

    destinations = {
        "sms": _address(state.contact_phone),
        "email": _address(state.contact_email),
    }
    first = state.preferred_channel or "sms"
    second = "email" if first == "sms" else "sms"
    for channel in (first, second):
        if address := destinations[channel]:
            return ContactPoint(channel, address)
    return None
