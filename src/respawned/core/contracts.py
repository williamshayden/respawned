"""Canonical source records shared by HTTP and in-process ingestion."""

from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Channel = Literal["email", "sms"]
Direction = Literal["inbound", "outbound"]
Status = Literal["open", "won", "lost"]
Classification = Literal["human", "automated", "unknown"]
Kind = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")
]


def _http_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and not any(character.isspace() for character in value)
        )
        if not valid or parsed.username is not None or parsed.password is not None:
            raise ValueError("source_url must be an http(s) URL without credentials")
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("source_url must be an http(s) URL without credentials") from exc
    return value


SourceUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
    AfterValidator(_http_url),
]
Summary = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class OpportunityContextIn(BaseModel):
    """Bounded display facts; source records cannot supply executable UI or policy."""

    model_config = ConfigDict(extra="forbid")

    company: Annotated[NonEmpty, Field(max_length=200)] | None = None
    role: Annotated[NonEmpty, Field(max_length=200)] | None = None
    stage: Annotated[NonEmpty, Field(max_length=100)] | None = None
    summary: Summary | None = None
    expected_reply_at: AwareDatetime | None = None
    source_url: SourceUrl | None = None


class OpportunityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NonEmpty
    contact_key: NonEmpty | None = None
    contact_name: NonEmpty | None = None
    contact_phone: NonEmpty | None = None
    contact_email: NonEmpty | None = None
    owner_name: NonEmpty | None = None
    value: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    status: Status
    created_at: AwareDatetime
    last_contact_at: AwareDatetime | None = None
    preferred_channel: Channel | None = None
    kind: Kind = "generic"
    title: Annotated[NonEmpty, Field(max_length=300)] | None = None
    context: OpportunityContextIn = Field(default_factory=OpportunityContextIn)

    @model_validator(mode="after")
    def validate_contact_route(self) -> "OpportunityIn":
        has_route = bool(self.contact_phone or self.contact_email)
        if bool(self.contact_key) != has_route:
            raise ValueError(
                "contact_key and a contact_phone or contact_email are required together"
            )
        if not has_route and self.preferred_channel is not None:
            raise ValueError("preferred_channel requires a contact route")
        return self


class ActivityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NonEmpty
    type: NonEmpty
    opportunity_id: NonEmpty
    occurred_at: AwareDatetime
    channel: Channel | None = None
    direction: Direction | None = None
    summary: Summary | None = None
    source_url: SourceUrl | None = None
    classification: Classification = "unknown"
