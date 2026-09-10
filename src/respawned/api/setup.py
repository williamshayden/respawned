"""Operator setup, authenticated import, and export without delivery authority."""

import os
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError, model_validator
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from respawned.api.models import IngestRequest, IngestResponse, OutboxResponse
from respawned.api.ui import require_review_authorization
from respawned.api.session import local_session
from respawned.api.paths import WORKFLOW_PREFIX
from respawned.core.ingest import IngestConflictError, UnknownOpportunityError, ingest_records
from respawned.core.outbox import list_outbox_rows, render_outbox_csv
from respawned.core.settings import (
    ModelSettings, environment_model_settings, load_model_settings, save_model_settings,
)
from respawned.db.helpers.pg_connect import build_engine_url
from respawned.core.workspaces import load_workspace_kinds


class ModelStatus(BaseModel):
    backend: Literal["openai_compatible", "codex_cli"] = "openai_compatible"
    source: Literal["saved", "environment"]
    base_url: str
    model_alias: str
    timeout_seconds: float
    api_key_env: Literal["LITELLM_MASTER_KEY", "RESPAWNED_MODEL_API_KEY"]
    key_configured: bool
    ready: bool
    verified: Literal[False] = False
    error: str | None = None


class UIImportRequest(IngestRequest):
    @model_validator(mode="after")
    def bound_import(self):
        if len(self.opportunities) + len(self.activities) > 1000:
            raise ValueError("Import at most 1000 records and activities at a time")
        if len(self.model_dump_json().encode("utf-8")) > 2_000_000:
            raise ValueError("Import JSON must be at most 2 MB")
        return self


def model_status(settings: ModelSettings | None) -> ModelStatus:
    """Report configuration only; reading or saving setup never probes a backend."""
    source = "saved" if settings is not None else "environment"
    try:
        value = settings or environment_model_settings()
    except ValidationError:
        # Invalid environment URLs may contain embedded secrets. Never echo them.
        return ModelStatus(source=source, base_url="", model_alias="", timeout_seconds=60,
                           api_key_env="LITELLM_MASTER_KEY", key_configured=False,
                           ready=False, error="Server model environment is invalid")
    key_configured = bool(os.environ.get(value.api_key_env, "").strip())
    if value.backend == "codex_cli":
        return ModelStatus(**value.model_dump(), source=source, key_configured=False,
                           ready=True)
    return ModelStatus(**value.model_dump(), source=source,
                       key_configured=key_configured, ready=key_configured)


def probe_setup_database() -> ModelSettings | None:
    """Bound connection and statement waits; never initialize schema or call a model."""
    engine = create_engine(
        build_engine_url(), poolclass=NullPool,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=2000 -c lock_timeout=2000"},
    )
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT to_regclass('application_settings')")).scalar_one() is None:
                return None
            return load_model_settings(connection)
    finally:
        engine.dispose()


def create_setup_routers(connection_dependency) -> tuple[APIRouter, APIRouter]:
    """Return workflow setup routes and the existing public discovery/export routes."""
    router = APIRouter()
    protected = APIRouter(dependencies=[Depends(require_review_authorization)])
    ConnectionDep = Annotated[Connection, Depends(connection_dependency, scope="function")]

    @router.get("/v1/setup/bootstrap")
    def bootstrap(request: Request) -> dict:
        # No database, credential values, configuration, or auth bypass here.
        return {
            "review_enabled": local_session(request) is not None or bool(os.environ.get("RESPAWNED_REVIEW_TOKEN", "").strip()),
            "workflow_api_prefix": WORKFLOW_PREFIX,
        }

    @protected.get("/setup")
    def setup(request: Request) -> dict:
        settings = None
        database = {"status": "ready", "message": "PostgreSQL is available"}
        try:
            settings = probe_setup_database()
        except (SQLAlchemyError, ValueError):
            database = {"status": "unavailable", "message": "Check the server DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD settings"}
        return {
            "database": database,
            "review": {"enabled": True, "authentication": "local_session" if local_session(request) is not None else "bearer", "token_env": "RESPAWNED_REVIEW_TOKEN"},
            "model": model_status(settings).model_dump(),
            "outbox": {
                "mode": "api_and_export", "automatic_delivery": False,
                "export_url": WORKFLOW_PREFIX + "/outbox/export", "pending_url": "/v1/outbox/pending",
                "receipt_url": "/v1/outbox/{id}/receipt", "token_env": "RESPAWNED_OUTBOX_TOKEN",
                "token_configured": bool(os.environ.get("RESPAWNED_OUTBOX_TOKEN", "").strip()),
            },
            "sources": {"mode": "api_import", "import_url": WORKFLOW_PREFIX + "/import"},
        }

    @protected.put("/setup/model", response_model=ModelStatus)
    def configure_model(payload: ModelSettings, connection: ConnectionDep) -> ModelStatus:
        save_model_settings(connection, payload)
        return model_status(payload)

    @protected.post("/import", response_model=IngestResponse)
    def import_records(payload: UIImportRequest, connection: ConnectionDep) -> IngestResponse:
        try:
            result = ingest_records(
                connection,
                opportunities=(record.model_dump() for record in payload.opportunities),
                activities=(record.model_dump() for record in payload.activities),
            )
        except (IngestConflictError, UnknownOpportunityError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return IngestResponse(opportunities_upserted=result.opportunities_upserted,
                              activities_inserted=result.activities_inserted)

    @router.get("/v1/outbox/export", dependencies=[Depends(require_review_authorization)])
    @protected.get("/outbox/export")
    def export_outbox(connection: ConnectionDep, format: Literal["json", "csv"] = "json",
                      workspace_id: UUID | None = None) -> Response:
        """Export a complete snapshot; neither format acknowledges delivery."""
        kinds = None
        if workspace_id is not None:
            kinds = load_workspace_kinds(connection, workspace_id)
            if kinds is None:
                raise HTTPException(404, "Workspace not found")
        rows = list_outbox_rows(connection, kinds=kinds)
        headers = {"Content-Disposition": f'attachment; filename="respawned-outbox.{format}"',
                   "Cache-Control": "no-store"}
        if format == "csv":
            return Response(content=render_outbox_csv(rows), media_type="text/csv", headers=headers)
        items = [OutboxResponse.model_validate(row) for row in rows]
        return JSONResponse(
            content=jsonable_encoder({"items": items}),
            headers=headers,
        )

    return protected, router
