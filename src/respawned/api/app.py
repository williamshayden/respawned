"""Local integration API with explicitly enabled operator processing."""

from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache
import hmac
import os
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from respawned.api.models import (
    DraftListResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    OutboxListResponse,
    ProcessRequest,
    ReadinessResponse,
)
from respawned.api.static import mount_review_assets
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.inbox import ReplyInboxResult, list_reply_inbox
from respawned.core.ingest import (
    IngestConflictError,
    UnknownOpportunityError,
    ingest_records,
)
from respawned.core.policy import Policy, load_policy
from respawned.core.workflow import ProcessResult, process_candidates, utc_now
from respawned.db.helpers.pg_connect import (
    DatabaseConnectionError, create_tables, get_engine,
)
from respawned.llm.adapter import LiteLLMAdapter


@lru_cache(maxsize=1)
def get_api_engine() -> Engine:
    try:
        engine = get_engine()
    except ValueError as exc:
        raise HTTPException(503, "Database configuration is invalid") from exc
    try:
        create_tables(engine)
    except Exception:
        engine.dispose()
        raise
    return engine


def get_connection(
    engine: Annotated[Engine, Depends(get_api_engine)],
) -> Iterator[Connection]:
    """Use function scope so the transaction commits before HTTP success is sent."""
    with engine.begin() as connection:
        yield connection


def get_workflow_policy() -> Policy:
    try:
        return load_policy(os.environ.get("RESPAWNED_POLICY_PATH", DEFAULT_POLICY_PATH))
    except ValueError as exc:
        raise HTTPException(503, "Workflow policy configuration is invalid") from exc


def get_workflow_adapter() -> LiteLLMAdapter:
    try:
        return LiteLLMAdapter.from_env()
    except ValueError as exc:
        raise HTTPException(503, "Drafting model is not configured") from exc


def get_workflow_clock() -> Callable[[], datetime]:
    """The operator can inject a fixed simulation clock; callers cannot."""
    return utc_now


def require_process_authorization(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """A separate operator credential enables processing; ingestion stays inert."""
    expected = os.environ.get("RESPAWNED_PROCESS_TOKEN", "").strip()
    if not expected:
        raise HTTPException(404, "Processing is disabled")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            401, "Operator authorization required", headers={"WWW-Authenticate": "Bearer"}
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        yield
    finally:
        # Dependency-overridden engines belong to their caller, not this app.
        if get_api_engine.cache_info().currsize:
            engine = get_api_engine()
            get_api_engine.cache_clear()
            engine.dispose()


app = FastAPI(title="Respawned", version="1.0.0", lifespan=lifespan)


@app.exception_handler(DatabaseConnectionError)
@app.exception_handler(SQLAlchemyError)
async def database_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    """Connection details, source rows, and driver diagnostics stay off the wire."""
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


@app.get("/healthz", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness only; does not open a database connection or call a model."""
    return HealthResponse()


@app.get("/readyz", response_model=ReadinessResponse)
def readiness(
    connection: Annotated[Connection, Depends(get_connection, scope="function")],
    policy: Annotated[Policy, Depends(get_workflow_policy)],
) -> ReadinessResponse:
    """Check the database schema and policy, independently of optional drafting.

    The first database access initializes the schema. Later probes check actual
    connectivity and required relations without scanning user records. Readiness
    does not imply model/provider availability, source freshness, or delivery.
    """
    connection.exec_driver_sql("SET LOCAL statement_timeout = '2s'")
    connection.exec_driver_sql("""
        SELECT opportunities.id, activities.id, sync_runs.id, candidates.id,
               drafts.id, outbox.authorization_mode, opportunity_states.opportunity_id
        FROM opportunities, activities, sync_runs, candidates, drafts, outbox,
             opportunity_states
        LIMIT 0
    """)
    return ReadinessResponse()


@app.get("/v1/inbox", response_model=ReplyInboxResult)
def reply_inbox(
    connection: Annotated[Connection, Depends(get_connection, scope="function")],
    policy: Annotated[Policy, Depends(get_workflow_policy)],
    now: Annotated[
        AwareDatetime | None,
        Query(description="Activity cutoff with a UTC offset; defaults to current UTC time."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ReplyInboxResult:
    """List unanswered ingested human replies without authorizing outreach.

    This uses current opportunity snapshots and the server policy. Source
    freshness is unknown; no mailbox refresh or delivery eligibility is implied.
    """
    return list_reply_inbox(
        connection,
        now=(now.astimezone(UTC) if now is not None else datetime.now(UTC)),
        policy=policy,
        limit=limit,
    )


@app.post(
    "/v1/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_200_OK,
)
def ingest(
    payload: IngestRequest,
    connection: Annotated[Connection, Depends(get_connection, scope="function")],
) -> IngestResponse:
    try:
        result = ingest_records(
            connection,
            opportunities=(record.model_dump() for record in payload.opportunities),
            activities=(record.model_dump() for record in payload.activities),
        )
    except (IngestConflictError, UnknownOpportunityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return IngestResponse(
        opportunities_upserted=result.opportunities_upserted,
        activities_inserted=result.activities_inserted,
    )


@app.post(
    "/v1/process", response_model=ProcessResult,
    dependencies=[Depends(require_process_authorization)],
)
def process(
    payload: ProcessRequest,
    engine: Annotated[Engine, Depends(get_api_engine)],
    policy: Annotated[Policy, Depends(get_workflow_policy)],
    adapter: Annotated[LiteLLMAdapter, Depends(get_workflow_adapter)],
    clock: Annotated[Callable[[], datetime], Depends(get_workflow_clock)],
) -> ProcessResult:
    """Draft a bounded queue under server policy; never sends messages.

    Disabled unless the operator sets RESPAWNED_PROCESS_TOKEN. Authorization is
    checked before opening the database or resolving the drafting model.
    Completed items commit independently and can be inspected after a retry.
    """
    return process_candidates(engine, policy=policy, adapter=adapter,
                              limit=payload.limit, clock=clock)


@app.get("/v1/drafts", response_model=DraftListResponse)
def drafts(
    connection: Annotated[Connection, Depends(get_connection, scope="function")],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DraftListResponse:
    """Inspect draft history. This read does not grant human approval authority."""
    rows = connection.execute(text("""
        SELECT id, candidate_id, contact_key, contact_address, contact_name,
               channel, primary_opportunity_id, opportunity_ids, body, status,
               created_at, updated_at, reviewed_at
        FROM drafts ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset
    """), {"limit": limit + 1, "offset": offset}).mappings().all()
    return DraftListResponse(items=rows[:limit], has_more=len(rows) > limit)


@app.get("/v1/outbox", response_model=OutboxListResponse)
def outbox(
    connection: Annotated[Connection, Depends(get_connection, scope="function")],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OutboxListResponse:
    """Inspect reservations and their authorization provenance, without delivery.

    Pagination is a snapshot read, not a synchronization checkpoint. Concurrent
    inserts or status changes require rereading/reconciling by stable item ID.
    """
    rows = connection.execute(text("""
        SELECT id, draft_id, contact_key, contact_address, contact_name,
               channel, opportunity_ids, body, status, authorization_mode,
               created_at, sent_at
        FROM outbox ORDER BY id LIMIT :limit OFFSET :offset
    """), {"limit": limit + 1, "offset": offset}).mappings().all()
    return OutboxListResponse(items=rows[:limit], has_more=len(rows) > limit)


def _register_review_interface() -> None:
    # Delay importing the router until its shared dependencies are defined.
    from respawned.api.ui import create_ui_router

    app.include_router(create_ui_router(
        get_connection, get_workflow_policy, get_workflow_clock,
    ))


_register_review_interface()

# Registered last so API authorization and error responses retain their routes.
mount_review_assets(app, os.environ.get("RESPAWNED_UI_DIST"))
