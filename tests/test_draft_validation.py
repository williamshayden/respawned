import pytest

from respawned.core.helpers.validate import (
    DraftValidationError,
    validate_draft,
)


def test_validate_draft_rejects_blank_message():
    with pytest.raises(DraftValidationError, match="blank"):
        validate_draft("   ", max_characters=160, quote_status="open")


def test_validate_draft_rejects_message_over_maximum_length():
    with pytest.raises(DraftValidationError, match="4 characters"):
        validate_draft("hello", max_characters=4, quote_status="open")


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
        validate_draft(body, max_characters=160, quote_status="open")


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
        validate_draft(body, max_characters=160, quote_status="open")


@pytest.mark.parametrize("status", ["accepted", "dismissed", " Accepted "])
def test_validate_draft_rejects_terminal_quote_statuses(status):
    with pytest.raises(DraftValidationError, match="terminal"):
        validate_draft(
            "Hi John, just checking in.",
            max_characters=160,
            quote_status=status,
        )


def test_validate_draft_requires_tech_name_only_when_enabled():
    body = "Hi John, just checking in."

    assert (
        validate_draft(
            body,
            max_characters=160,
            quote_status="open",
            tech_name="Bob",
        )
        == body
    )

    with pytest.raises(DraftValidationError, match="tech name"):
        validate_draft(
            body,
            max_characters=160,
            quote_status="open",
            tech_name="Bob",
            require_tech_name=True,
        )
