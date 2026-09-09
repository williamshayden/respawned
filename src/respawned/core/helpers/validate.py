"""Deterministic guardrails for generated follow-up copy."""

import re


_PLACEHOLDER_PATTERN = re.compile(
    r"(?:\{\{?[^{}\n]+\}?\}|\[[^\]\n]+\]|<[^>\n]+>|"
    r"\b(?:TODO|TBD|PLACEHOLDER|INSERT\s+(?:CUSTOMER\s+)?NAME)\b)",
    re.IGNORECASE,
)
_CURRENCY_PATTERN = re.compile(
    r"(?:[$€£¥]|\b(?:USD|EUR|GBP|CAD|AUD)\b|"
    r"\b(?:dollars?|cents?|euros?|pounds?|yen)\b)",
    re.IGNORECASE,
)


class DraftValidationError(ValueError):
    """Raised when generated copy is unsafe to present for approval."""


def _status_error(opportunity_status: str) -> str | None:
    normalized_status = opportunity_status.strip().lower()
    if normalized_status != "open":
        return f"cannot draft for opportunity status {normalized_status!r}"
    return None


def ensure_opportunity_is_contactable(opportunity_status: str) -> None:
    """Reject an opportunity that must never receive follow-up copy."""
    error = _status_error(opportunity_status)
    if error:
        raise DraftValidationError(error)


def validate_draft(
    body: str,
    *,
    max_characters: int,
    opportunity_status: str,
    owner_name: str | None = None,
    require_owner_name: bool = False,
) -> str:
    """Return normalized copy or raise for any deterministic guardrail failure."""
    normalized_body = body.strip()
    errors: list[str] = []

    if status_error := _status_error(opportunity_status):
        errors.append(status_error)
    if not normalized_body:
        errors.append("draft message cannot be blank")
    if len(normalized_body) > max_characters:
        errors.append(f"draft message exceeds {max_characters} characters")
    if _PLACEHOLDER_PATTERN.search(normalized_body):
        errors.append("draft message contains a placeholder")
    if _CURRENCY_PATTERN.search(normalized_body):
        errors.append("draft message contains a currency amount")
    if require_owner_name and (
        not owner_name or owner_name.casefold() not in normalized_body.casefold()
    ):
        errors.append("draft message must include the owner name")

    if errors:
        raise DraftValidationError("; ".join(errors))
    return normalized_body
