"""Deterministic guardrails for generated follow-up copy."""

import re


TERMINAL_QUOTE_STATUSES = frozenset({"accepted", "dismissed"})

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


def _terminal_status_error(quote_status: str) -> str | None:
    normalized_status = quote_status.strip().lower()
    if normalized_status in TERMINAL_QUOTE_STATUSES:
        return f"cannot draft for terminal quote status {normalized_status!r}"
    return None


def ensure_quote_is_contactable(quote_status: str) -> None:
    """Reject a quote that must never receive follow-up copy."""
    error = _terminal_status_error(quote_status)
    if error:
        raise DraftValidationError(error)


def validate_draft(
    body: str,
    *,
    max_characters: int,
    quote_status: str,
    tech_name: str | None = None,
    require_tech_name: bool = False,
) -> str:
    """Return normalized copy or raise for any deterministic guardrail failure."""
    normalized_body = body.strip()
    errors: list[str] = []

    terminal_status_error = _terminal_status_error(quote_status)
    if terminal_status_error:
        errors.append(terminal_status_error)
    if not normalized_body:
        errors.append("draft message cannot be blank")
    if len(normalized_body) > max_characters:
        errors.append(f"draft message exceeds {max_characters} characters")
    if _PLACEHOLDER_PATTERN.search(normalized_body):
        errors.append("draft message contains a placeholder")
    if _CURRENCY_PATTERN.search(normalized_body):
        errors.append("draft message contains a currency amount")
    if require_tech_name and (
        not tech_name or tech_name.casefold() not in normalized_body.casefold()
    ):
        errors.append("draft message must include the tech name")

    if errors:
        raise DraftValidationError("; ".join(errors))
    return normalized_body
