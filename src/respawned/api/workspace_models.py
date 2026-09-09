"""Saved views over one shared engine, without predefined use cases."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


RecordKind = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class WorkspaceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: str = Field(default="", max_length=2000)
    kinds: list[RecordKind] = Field(default_factory=list, max_length=100)

    @field_validator("kinds")
    @classmethod
    def unique_kinds(cls, kinds: list[str]) -> list[str]:
        if len(kinds) != len(set(kinds)):
            raise ValueError("record kinds must be unique")
        return kinds


class Workspace(BaseModel):
    id: UUID
    name: str
    description: str
    kinds: list[str]
    created_at: datetime
    updated_at: datetime


class WorkspaceList(BaseModel):
    items: list[Workspace]
    available_kinds: list[str]
    scope: Literal["shared_engine"] = "shared_engine"
