"""Explicitly authenticated human review; every write commits before response."""

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
import hmac
import os
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.api.ui_models import (
    UIConfig, UIDraft, UIEditRequest, UIInboxResult, UIOutboxList,
    UIRecord, UIRecordList, UIReviewRequest,
    UISyncRequest, UISyncResult,
)
from respawned.core.inbox import list_reply_inbox
from respawned.core.policy import Policy
from respawned.core.review import (
    ReviewBlockedError, approve_draft, draft_candidate,
    reject_draft, update_draft_message,
)
from respawned.core.sync import sync_candidates
from respawned.core.ui_queries import (
    draft_view, inbox_review_targets, list_ui_records, load_ui_draft, record_references,
)
from respawned.core.workspaces import load_workspace_kinds
from respawned.llm.adapter import LiteLLMAdapter


def require_review_authorization(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = os.environ.get("RESPAWNED_REVIEW_TOKEN", "").strip()
    if not expected:
        raise HTTPException(404, "Review interface is disabled")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(401, "Reviewer authorization required",
                            headers={"WWW-Authenticate": "Bearer"})


def get_draft_adapter_factory() -> Callable[[], LiteLLMAdapter]:
    """Resolve the model only for a new draft, never for loading existing copy."""
    from respawned.api.app import get_workflow_adapter

    return get_workflow_adapter


def _review_error(exc: ReviewBlockedError) -> HTTPException:
    detail = str(exc)
    if detail.startswith("draft generation failed:"):
        return HTTPException(503, "Draft generation failed. Check the configured drafting provider.")
    return HTTPException(422 if detail.startswith("draft failed validation:") else 409, detail)


def _require_draft(connection: Connection, draft_id: UUID):
    draft = load_ui_draft(connection, draft_id)
    if draft is None:
        raise HTTPException(404, "Draft not found")
    return draft


def _draft_response(connection: Connection, draft_id: UUID) -> UIDraft:
    draft = _require_draft(connection, draft_id)
    outbox_id = connection.execute(text("SELECT id FROM outbox WHERE draft_id = :id"),
                                   {"id": draft_id}).scalar_one_or_none()
    return UIDraft(**draft_view(draft, outbox_id=outbox_id))


def create_ui_router(connection_dependency, policy_dependency, clock_dependency) -> APIRouter:
    """Reuse app dependencies without a circular module import."""
    router = APIRouter(prefix="/v1/ui", dependencies=[Depends(require_review_authorization)])
    ConnectionDep = Annotated[Connection, Depends(connection_dependency, scope="function")]
    PolicyDep = Annotated[Policy, Depends(policy_dependency)]
    ClockDep = Annotated[Callable[[], datetime], Depends(clock_dependency)]

    @router.get("/config", response_model=UIConfig)
    def config(policy: PolicyDep) -> UIConfig:
        return UIConfig(policy_mode=policy.review.mode, cooldown_hours=float(policy.cooldown_hours),
                        max_draft_characters=policy.drafting.max_characters)


    @router.get("/records", response_model=UIRecordList)
    def records(connection: ConnectionDep, policy: PolicyDep, clock: ClockDep,
                limit: Annotated[int, Query(ge=1, le=200)] = 50,
                offset: Annotated[int, Query(ge=0)] = 0,
                workspace_id: Annotated[UUID | None, Query(
                    description="Saved view filter; eligibility remains shared across all records.",
                )] = None) -> UIRecordList:
        kinds = None
        if workspace_id is not None:
            kinds = load_workspace_kinds(connection, workspace_id)
            if kinds is None:
                raise HTTPException(404, "Workspace not found")
        try:
            result = list_ui_records(connection, now=clock(), policy=policy,
                                     limit=limit, offset=offset, kinds=kinds)
        except ValueError as exc:
            raise HTTPException(503, "Workflow policy or tracked state is invalid") from exc
        return UIRecordList(**result)


    @router.get("/records/{record_id:path}", response_model=UIRecord)
    def record(record_id: str, connection: ConnectionDep, policy: PolicyDep,
               clock: ClockDep) -> UIRecord:
        """Open an inbox target without loading or changing the queue page."""
        try:
            result = list_ui_records(connection, now=clock(), policy=policy,
                                     record_id=record_id, limit=1)
        except ValueError as exc:
            raise HTTPException(503, "Workflow policy or tracked state is invalid") from exc
        if not result["items"]:
            raise HTTPException(404, "Record not found")
        return UIRecord(**result["items"][0])


    @router.get("/inbox", response_model=UIInboxResult)
    def inbox(connection: ConnectionDep, policy: PolicyDep, clock: ClockDep,
              limit: Annotated[int, Query(ge=1, le=200)] = 200,
              workspace_id: Annotated[UUID | None, Query(
                  description="Saved view filter; reply groups retain contact-wide evidence.",
              )] = None) -> UIInboxResult:
        """Outstanding ingested replies remain visible during outreach cooldowns."""
        kinds = None
        if workspace_id is not None:
            kinds = load_workspace_kinds(connection, workspace_id)
            if kinds is None:
                raise HTTPException(404, "Workspace not found")
        try:
            now = clock()
            result = list_reply_inbox(connection, now=now, policy=policy, limit=limit, kinds=kinds)
            targets = inbox_review_targets(connection, now=now, policy=policy)
        except ValueError as exc:
            raise HTTPException(503, "Workflow policy or tracked state is invalid") from exc
        refs = record_references(connection, (record_id for item in result.items
                                              for record_id in item.opportunity_ids))
        values = asdict(result)
        values["items"] = [dict(
            asdict(item), record_refs=[refs[record_id] for record_id in item.opportunity_ids if record_id in refs],
            review_record_id=targets.get(item.contact_key, item.opportunity_ids[0]),
        ) for item in result.items]
        return UIInboxResult(**values)


    @router.post("/sync", response_model=UISyncResult)
    def sync(payload: UISyncRequest, connection: ConnectionDep, policy: PolicyDep,
             clock: ClockDep) -> UISyncResult:
        try:
            result = sync_candidates(connection, now=clock(), policy=policy, limit=payload.limit)
        except ValueError as exc:
            raise HTTPException(503, "Workflow policy or tracked state is invalid") from exc
        assert result.run_id is not None
        return UISyncResult(candidate_count=len(result.candidates),
                            inserted_count=result.inserted_count, run_id=result.run_id)


    @router.post("/records/{record_id:path}/draft", response_model=UIDraft)
    def create_draft(
        record_id: str, connection: ConnectionDep, policy: PolicyDep, clock: ClockDep,
        adapter_factory: Annotated[Callable[[], LiteLLMAdapter], Depends(get_draft_adapter_factory)],
    ) -> UIDraft:
        if not connection.execute(text("SELECT 1 FROM opportunities WHERE id = :id"),
                                  {"id": record_id}).scalar_one_or_none():
            raise HTTPException(404, "Record not found")
        now = clock()
        try:
            current = sync_candidates(connection, now=now, policy=policy,
                                      primary_opportunity_id=record_id, limit=1).candidates
        except ValueError as exc:
            raise HTTPException(503, "Workflow policy or tracked state is invalid") from exc
        candidate = current[0] if current else None
        if candidate is None:
            raise HTTPException(409, "This record is not currently eligible for a draft. Review its current state and contact eligibility.")
        existing_id = connection.execute(text("SELECT id FROM drafts WHERE candidate_id = :id"),
                                         {"id": candidate.id}).scalar_one_or_none()
        if existing_id is not None:
            return _draft_response(connection, existing_id)
        try:
            draft = draft_candidate(connection, candidate=candidate, now=now,
                                    policy=policy, adapter=adapter_factory())
        except ReviewBlockedError as exc:
            raise _review_error(exc) from exc
        if draft is None:
            raise HTTPException(409, "Candidate has already been reviewed")
        return _draft_response(connection, draft.id)


    @router.post("/drafts/{draft_id}/edit", response_model=UIDraft)
    def edit(draft_id: UUID, payload: UIEditRequest, connection: ConnectionDep,
             policy: PolicyDep, clock: ClockDep) -> UIDraft:
        _require_draft(connection, draft_id)
        try:
            update_draft_message(connection, draft_id=draft_id, body=payload.body,
                                 expected_review_token=payload.review_token, now=clock(), policy=policy)
        except ReviewBlockedError as exc:
            raise _review_error(exc) from exc
        return _draft_response(connection, draft_id)


    @router.post("/drafts/{draft_id}/approve", response_model=UIDraft)
    def approve(draft_id: UUID, payload: UIReviewRequest, connection: ConnectionDep,
                policy: PolicyDep, clock: ClockDep) -> UIDraft:
        _require_draft(connection, draft_id)
        try:
            approve_draft(connection, draft_id=draft_id, expected_review_token=payload.review_token,
                          now=clock(), policy=policy)
        except ReviewBlockedError as exc:
            raise _review_error(exc) from exc
        return _draft_response(connection, draft_id)


    @router.post("/drafts/{draft_id}/reject", response_model=UIDraft)
    def reject(draft_id: UUID, payload: UIReviewRequest, connection: ConnectionDep,
               clock: ClockDep) -> UIDraft:
        _require_draft(connection, draft_id)
        try:
            reject_draft(connection, draft_id=draft_id, expected_review_token=payload.review_token,
                         now=clock())
        except ReviewBlockedError as exc:
            raise _review_error(exc) from exc
        return _draft_response(connection, draft_id)


    @router.get("/outbox", response_model=UIOutboxList)
    def outbox(connection: ConnectionDep,
               limit: Annotated[int, Query(ge=1, le=200)] = 50,
               offset: Annotated[int, Query(ge=0)] = 0) -> UIOutboxList:
        rows = connection.execute(text("""
            SELECT id, draft_id, contact_key, contact_address, contact_name, channel,
                   opportunity_ids, body, status, authorization_mode, created_at, sent_at
            FROM outbox ORDER BY id DESC LIMIT :limit OFFSET :offset
        """), {"limit": limit + 1, "offset": offset}).mappings().all()
        refs = record_references(connection, (record_id for row in rows[:limit]
                                              for record_id in row["opportunity_ids"]))
        return UIOutboxList(items=[dict(
            row, record_refs=[refs[record_id] for record_id in row["opportunity_ids"] if record_id in refs],
        ) for row in rows[:limit]], has_more=len(rows) > limit)

    return router
