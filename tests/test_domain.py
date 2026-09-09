from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from respawned.core.domain import (
    Activity,
    ContactPoint,
    OpportunityState,
    resolve_contact,
)


def test_email_only_contact_uses_email() -> None:
    state = OpportunityState(
        opportunity_id="opp-1",
        contact_email="person@example.com",
    )

    assert resolve_contact(state) == ContactPoint("email", "person@example.com")


def test_missing_preference_defaults_to_sms() -> None:
    state = OpportunityState(
        opportunity_id="opp-1",
        contact_phone="+15555550100",
        contact_email="person@example.com",
    )

    assert resolve_contact(state) == ContactPoint("sms", "+15555550100")


def test_preferred_email_is_selected() -> None:
    state = OpportunityState(
        opportunity_id="opp-1",
        contact_phone="+15555550100",
        contact_email="person@example.com",
        preferred_channel="email",
    )

    assert resolve_contact(state) == ContactPoint("email", "person@example.com")


@pytest.mark.parametrize(
    ("preferred", "phone", "email", "expected"),
    [
        ("email", "+15555550100", None, ContactPoint("sms", "+15555550100")),
        ("sms", None, "person@example.com", ContactPoint("email", "person@example.com")),
    ],
)
def test_unavailable_preference_falls_back(
    preferred: str,
    phone: str | None,
    email: str | None,
    expected: ContactPoint,
) -> None:
    state = OpportunityState(
        opportunity_id="opp-1",
        contact_phone=phone,
        contact_email=email,
        preferred_channel=preferred,
    )

    assert resolve_contact(state) == expected


def test_no_destination_suppresses_contact() -> None:
    assert resolve_contact(OpportunityState(opportunity_id="opp-1")) is None


def test_domain_objects_are_immutable() -> None:
    activity = Activity("act-1", "viewed", datetime.now(UTC))
    state = OpportunityState(
        opportunity_id="opp-1",
        activities=[activity],  # type: ignore[arg-type]
    )
    point = ContactPoint("sms", "+15555550100")

    assert state.activities == (activity,)
    with pytest.raises(FrozenInstanceError):
        state.status = "closed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        activity.activity_type = "replied"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        point.address = "+15555550101"  # type: ignore[misc]


@pytest.mark.parametrize("channel", ["voice", "", "   "])
def test_contact_point_rejects_unsupported_channels(channel: str) -> None:
    with pytest.raises(ValueError, match="unsupported contact channel"):
        ContactPoint(channel, "destination")


def test_contact_point_rejects_empty_address() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ContactPoint("email", "   ")
