"""Source-neutral HTTP models for opportunity ingestion."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from respawned.core.contracts import ActivityIn, OpportunityIn


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunities: list[OpportunityIn] = Field(default_factory=list)
    activities: list[ActivityIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_not_empty(self) -> "IngestRequest":
        if not (self.opportunities or self.activities):
            raise ValueError("at least one opportunity or activity is required")
        return self


class IngestResponse(BaseModel):
    opportunities_upserted: int
    activities_inserted: int


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    engine_version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"


class ProcessRequest(BaseModel):
    """Processing parameters cannot override server policy or its clock."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=10, ge=1, le=50, strict=True)


class DraftResponse(BaseModel):
    id: UUID
    candidate_id: UUID
    contact_key: str
    contact_address: str
    contact_name: str | None
    channel: Literal["email", "sms"]
    primary_opportunity_id: str
    opportunity_ids: list[str]
    body: str
    status: Literal["pending", "approved", "rejected"]
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None


class DraftListResponse(BaseModel):
    items: list[DraftResponse]
    has_more: bool


class OutboxResponse(BaseModel):
    id: int
    draft_id: UUID
    contact_key: str
    contact_address: str
    contact_name: str | None
    channel: Literal["email", "sms"]
    opportunity_ids: list[str]
    body: str
    status: Literal["pending", "sent", "failed"]
    authorization_mode: Literal["human", "automatic", "legacy_unknown"]
    created_at: datetime
    sent_at: datetime | None


class OutboxListResponse(BaseModel):
    """An ordered snapshot, not a work claim or a delivery acknowledgment."""

    items: list[OutboxResponse]
    has_more: bool
