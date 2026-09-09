"""The shared review interface consumes one context-neutral response contract."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from respawned.api.models import OutboxResponse
from respawned.core.inbox import ReplyEvidence


class UIRecordRef(BaseModel):
    id: str
    kind: str
    title: str


class UIOutboxItem(OutboxResponse):
    record_refs: list[UIRecordRef]


class UIOutboxList(BaseModel):
    items: list[UIOutboxItem]
    has_more: bool


class UIInboxItem(BaseModel):
    contact_key: str
    contact_name: str | None
    channel: Literal["email", "sms"]
    contact_address: str
    opportunity_ids: list[str]
    latest_reply_at: datetime
    last_outbound_at: datetime | None
    reply_evidence: list[ReplyEvidence]
    pending_outbox_count: int
    record_refs: list[UIRecordRef]
    review_record_id: str


class UIInboxResult(BaseModel):
    as_of: datetime
    items: list[UIInboxItem]
    total: int
    has_more: bool
    source_freshness: Literal["unknown"] = "unknown"
    outreach_eligibility: Literal["not_evaluated"] = "not_evaluated"


class UIConfig(BaseModel):
    policy_mode: Literal["human", "automatic"]
    cooldown_hours: float
    max_draft_characters: int
    source_freshness: Literal["unknown"] = "unknown"


class UIOverviewCounts(BaseModel):
    records: int = Field(ge=0)
    ready: int = Field(ge=0)
    pending_drafts: int = Field(ge=0)
    reply_contacts: int = Field(ge=0)
    pending_outbox: int = Field(ge=0)


class UIWorkspaceOverview(BaseModel):
    id: str
    name: str
    description: str
    kinds: list[str]
    counts: UIOverviewCounts


class UIOverview(BaseModel):
    generated_at: datetime
    source_freshness: Literal["unknown"] = "unknown"
    workspaces: list[UIWorkspaceOverview]


class UIContact(BaseModel):
    key: str
    name: str | None
    address: str
    channel: Literal["email", "sms"]


class UIField(BaseModel):
    label: str
    value: str


class UIReason(BaseModel):
    code: str
    label: str
    detail: str


class UIActivity(BaseModel):
    id: str
    label: str
    type: str
    occurred_at: datetime
    classification: Literal["human", "automated", "unknown"]
    source_url: str | None
    summary: str | None


class UIDraft(BaseModel):
    id: UUID
    body: str
    status: Literal["pending", "approved", "rejected"]
    review_token: str
    validation_errors: list[str] = Field(default_factory=list)
    outbox_id: int | None = None


class UIRecord(BaseModel):
    id: str
    kind: str
    title: str
    status: str
    stage: str | None
    source_url: str | None = None
    contact: UIContact | None
    fields: list[UIField]
    reason: UIReason
    next_action: Literal[
        "reply", "follow_up", "waiting", "blocked", "closed", "approved", "rejected"
    ]
    score: float | None
    last_contact_at: datetime | None
    candidate_id: UUID | None
    referenced_record_ids: list[str]
    activities: list[UIActivity]
    source_freshness: Literal["unknown"] = "unknown"
    draft: UIDraft | None


class UIRecordList(BaseModel):
    items: list[UIRecord]
    total: int
    has_more: bool
    as_of: datetime


class UISyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=200, ge=1, le=200, strict=True)


class UISyncResult(BaseModel):
    candidate_count: int
    inserted_count: int
    run_id: UUID


class UIReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_token: str = Field(pattern=r"^[a-f0-9]{64}$")


class UIEditRequest(UIReviewRequest):
    body: str = Field(max_length=10000)
