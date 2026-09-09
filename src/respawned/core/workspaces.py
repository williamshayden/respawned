"""Persist named record filters without changing ingestion or review authority."""

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection


def list_workspaces(connection: Connection) -> dict:
    items = connection.execute(text("""
        SELECT id, name, description, kinds, created_at, updated_at
        FROM workspaces ORDER BY created_at, id
    """)).mappings().all()
    kinds = connection.execute(text("""
        SELECT DISTINCT kind FROM opportunities ORDER BY kind
    """)).scalars().all()
    return {"items": [dict(item) for item in items], "available_kinds": kinds}


def load_workspace_kinds(connection: Connection, workspace_id: UUID) -> list[str] | None:
    """An empty list selects every kind; None means the saved view is missing."""
    return connection.execute(text("SELECT kinds FROM workspaces WHERE id = :id"),
                              {"id": workspace_id}).scalar_one_or_none()


def create_workspace(connection: Connection, *, name: str, description: str,
                     kinds: list[str]) -> dict:
    row = connection.execute(text("""
        INSERT INTO workspaces (id, name, description, kinds)
        VALUES (:id, :name, :description, :kinds)
        RETURNING id, name, description, kinds, created_at, updated_at
    """), {"id": uuid4(), "name": name, "description": description,
           "kinds": kinds}).mappings().one()
    return dict(row)


def update_workspace(connection: Connection, workspace_id: UUID, *, name: str,
                     description: str, kinds: list[str]) -> dict | None:
    row = connection.execute(text("""
        UPDATE workspaces
        SET name = :name, description = :description, kinds = :kinds,
            updated_at = clock_timestamp()
        WHERE id = :id
        RETURNING id, name, description, kinds, created_at, updated_at
    """), {"id": workspace_id, "name": name, "description": description,
           "kinds": kinds}).mappings().one_or_none()
    return dict(row) if row is not None else None


def delete_workspace(connection: Connection, workspace_id: UUID) -> bool:
    return connection.execute(text("""
        DELETE FROM workspaces WHERE id = :id RETURNING id
    """), {"id": workspace_id}).scalar_one_or_none() is not None
