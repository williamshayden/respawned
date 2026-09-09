import pytest

from respawned.core.helpers.validate import (
    DraftValidationError,
    validate_draft,
)


def test_validate_draft_rejects_blank_message():
    with pytest.raises(DraftValidationError, match="blank"):
        validate_draft("   ", max_characters=160, opportunity_status="open")


def test_validate_draft_rejects_message_over_maximum_length():
    with pytest.raises(DraftValidationError, match="4 characters"):
        validate_draft("hello", max_characters=4, opportunity_status="open")


@pytest.mark.parametrize(
    "body",
    [
        "Hi [Customer Name], just checking in.",
        "Hi {{customer_name}}, just checking in.",
        "Hi <customer_name>, just checking in.",
        "Hi there, INSERT NAME here.",
    ],
)
def test_validate_draft_rejects_placeholders(body):
    with pytest.raises(DraftValidationError, match="placeholder"):
        validate_draft(body, max_characters=160, opportunity_status="open")


@pytest.mark.parametrize(
    "body",
    [
        "Your quote is $1,200.",
        "Your quote is 1200 dollars.",
        "Your quote is USD 1200.",
    ],
)
def test_validate_draft_rejects_currency_amounts(body):
    with pytest.raises(DraftValidationError, match="currency"):
        validate_draft(body, max_characters=160, opportunity_status="open")


@pytest.mark.parametrize("status", ["won", "lost", "unknown"])
def test_validate_draft_rejects_non_open_opportunity_statuses(status):
    with pytest.raises(DraftValidationError, match="opportunity status"):
        validate_draft(
            "Hi John, just checking in.",
            max_characters=160,
            opportunity_status=status,
        )


def test_validate_draft_requires_owner_name_only_when_enabled():
    body = "Hi John, just checking in."

    assert (
        validate_draft(
            body,
            max_characters=160,
            opportunity_status="open",
            owner_name="Bob",
        )
        == body
    )

    with pytest.raises(DraftValidationError, match="owner name"):
        validate_draft(
            body,
            max_characters=160,
            opportunity_status="open",
            owner_name="Bob",
            require_owner_name=True,
        )
