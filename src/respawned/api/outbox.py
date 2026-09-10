"""Connector access to approved snapshots and externally confirmed send receipts."""

from collections.abc import Callable
from datetime import datetime
import hmac
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from sqlalchemy.engine import Connection

from respawned.api.models import OutboxListResponse, OutboxResponse
from respawned.api.session import local_session
from respawned.api.ui import require_review_authorization
from respawned.core.contracts import OutboxReceiptIn
from respawned.core.delivery import (
    OutboxNotFoundError, OutboxReceiptConflictError, get_outbox_item,
    list_pending_outbox, record_outbox_receipt,
)


class OutboxReceiptResponse(OutboxReceiptIn):
    recorded_at: datetime


class OutboxDetailResponse(OutboxResponse):
    receipt: OutboxReceiptResponse | None


def require_outbox_authorization(
    request: Request, authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Connector credentials cannot authorize drafting, review, or configuration."""
    expected = os.environ.get("RESPAWNED_OUTBOX_TOKEN", "").strip()
    scheme, _, supplied = (authorization or "").partition(" ")
    if expected and scheme.lower() == "bearer" and hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8"),
    ):
        return
    try:
        require_review_authorization(request, authorization)
    except HTTPException as exc:
        if exc.status_code not in (401, 404):
            raise
        enabled = (expected or os.environ.get("RESPAWNED_REVIEW_TOKEN", "").strip()
                   or local_session(request) is not None)
        if not enabled:
            raise HTTPException(404, "Outbox connector access is disabled") from exc
        raise HTTPException(401, "Outbox authorization required",
                            headers={"WWW-Authenticate": "Bearer"}) from exc


def create_outbox_router(connection_dependency, clock_dependency) -> APIRouter:
    router = APIRouter(prefix="/v1/outbox", dependencies=[Depends(require_outbox_authorization)])
    ConnectionDep = Annotated[Connection, Depends(connection_dependency, scope="function")]
    ClockDep = Annotated[Callable[[], datetime], Depends(clock_dependency)]

    @router.get("/pending", response_model=OutboxListResponse)
    def pending(connection: ConnectionDep,
                limit: Annotated[int, Query(ge=1, le=200)] = 50) -> OutboxListResponse:
        """Poll approved pending messages; this does not claim them or send them.

        Fetch again from the beginning after recording results. Coordinate one
        logical sender and use provider idempotency; no delivery cursor is issued.
        Reservations with unknown legacy authorization are excluded.
        """
        rows = list_pending_outbox(connection, limit=limit + 1)
        return OutboxListResponse(items=rows[:limit], has_more=len(rows) > limit)

    @router.get("/{outbox_id}", response_model=OutboxDetailResponse)
    def detail(outbox_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
               connection: ConnectionDep) -> OutboxDetailResponse:
        """Read the approved snapshot and its recorded sender result, if any."""
        try:
            return OutboxDetailResponse(**get_outbox_item(connection, outbox_id))
        except OutboxNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/{outbox_id}/receipt", response_model=OutboxDetailResponse)
    def receipt(outbox_id: Annotated[int, Path(gt=0, le=9223372036854775807)], payload: OutboxReceiptIn,
                connection: ConnectionDep, clock: ClockDep) -> OutboxDetailResponse:
        """Record a confirmed external send and outbound evidence atomically.

        Identical retries return the existing receipt. Conflicting results are
        rejected. This endpoint does not contact or verify a sending provider.
        """
        try:
            item = record_outbox_receipt(connection, outbox_id=outbox_id, receipt=payload, now=clock())
        except OutboxNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except OutboxReceiptConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return OutboxDetailResponse(**item)

    return router
