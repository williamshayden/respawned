"""Limited, non-sensitive context made available to the drafting model."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class DraftPayload:
    """The complete context allowed into a follow-up drafting prompt."""

    customer_name: str
    tech_name: str | None
    tone: str
    other_open_quote_count: int
    max_characters: int
    sign_off: str
    require_tech_name: bool = False
