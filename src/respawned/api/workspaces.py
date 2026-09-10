"""Reviewer-owned saved views; removing a workspace never removes records."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.engine import Connection

from respawned.api.ui import require_review_authorization
from respawned.api.workspace_models import Workspace, WorkspaceList, WorkspaceWrite
from respawned.core.workspaces import (
    create_workspace, delete_workspace, list_workspaces, update_workspace,
)


def create_workspace_router(connection_dependency, *, prefix: str = "/v1/ui/workspaces") -> APIRouter:
    router = APIRouter(prefix=prefix,
                       dependencies=[Depends(require_review_authorization)])
    ConnectionDep = Annotated[Connection, Depends(connection_dependency, scope="function")]

    @router.get("", response_model=WorkspaceList)
    def list_saved_views(connection: ConnectionDep) -> WorkspaceList:
        return WorkspaceList(**list_workspaces(connection))

    @router.post("", response_model=Workspace, status_code=status.HTTP_201_CREATED)
    def create_saved_view(payload: WorkspaceWrite, connection: ConnectionDep) -> Workspace:
        return Workspace(**create_workspace(connection, **payload.model_dump()))

    @router.put("/{workspace_id}", response_model=Workspace)
    def update_saved_view(workspace_id: UUID, payload: WorkspaceWrite,
                          connection: ConnectionDep) -> Workspace:
        workspace = update_workspace(connection, workspace_id, **payload.model_dump())
        if workspace is None:
            raise HTTPException(404, "Workspace not found")
        return Workspace(**workspace)

    @router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_saved_view(workspace_id: UUID, connection: ConnectionDep) -> Response:
        if not delete_workspace(connection, workspace_id):
            raise HTTPException(404, "Workspace not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
