"""One workflow HTTP surface shared by browser, CLI and agent clients."""

from fastapi import APIRouter

from respawned.api.session import create_session_router
from respawned.api.setup import create_setup_routers
from respawned.api.ui import create_ui_router
from respawned.api.workspaces import create_workspace_router


def create_workflow_routers(connection_dependency, policy_dependency, clock_dependency) -> tuple[APIRouter, APIRouter]:
    """Build the handlers once so compatibility aliases run the same code."""
    workflow = APIRouter()
    workflow.include_router(create_session_router(prefix="/session"))
    workflow.include_router(create_ui_router(
        connection_dependency, policy_dependency, clock_dependency, prefix="",
    ))
    workflow.include_router(create_workspace_router(connection_dependency, prefix="/workspaces"))
    setup, public = create_setup_routers(connection_dependency)
    workflow.include_router(setup)
    return workflow, public
