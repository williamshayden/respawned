"""Limited, non-sensitive context made available to the drafting model."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class DraftPayload:
    """The complete context allowed into a follow-up drafting prompt."""

    contact_name: str | None
    owner_name: str | None
    tone: str
    other_open_opportunity_count: int
    max_characters: int
    sign_off: str
    require_owner_name: bool = False
