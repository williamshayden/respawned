"""Canonical source records shared by HTTP and in-process ingestion."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
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


class OpportunityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NonEmpty
    contact_key: NonEmpty
    contact_name: NonEmpty | None = None
    contact_phone: NonEmpty | None = None
    contact_email: NonEmpty | None = None
    owner_name: NonEmpty | None = None
    value: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None
    status: Status
    created_at: AwareDatetime
    last_contact_at: AwareDatetime | None = None
    preferred_channel: Channel | None = None

    @model_validator(mode="after")
    def validate_contact_route(self) -> "OpportunityIn":
        if not (self.contact_phone or self.contact_email):
            raise ValueError("contact_phone or contact_email is required")
        return self


class ActivityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NonEmpty
    type: NonEmpty
    opportunity_id: NonEmpty
    occurred_at: AwareDatetime
    channel: Channel | None = None
    direction: Direction | None = None
