"""Persist non-secret model settings shared by the browser, API, and CLI."""

from collections.abc import Mapping
import os
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.llm.adapter import (
    DEFAULT_MODEL_ALIAS, DEFAULT_PROXY_URL, DEFAULT_TIMEOUT_SECONDS, LiteLLMAdapter,
)
from respawned.llm.codex import CodexDraftingAdapter


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    backend: Literal["openai_compatible", "codex_cli"] = "openai_compatible"
    base_url: str = Field(default="", max_length=2048)
    model_alias: str = Field(default="", max_length=200)
    timeout_seconds: float = Field(default=60, gt=0, le=300, allow_inf_nan=False)
    # Deliberately exclude arbitrary environment variables: selecting a backend
    # must not turn the reviewer into a reader of DB or operator credentials.
    api_key_env: Literal["LITELLM_MASTER_KEY", "RESPAWNED_MODEL_API_KEY"] = "LITELLM_MASTER_KEY"

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value:
            return value
        try:
            url = urlsplit(value)
            valid_port = url.port is None or 0 < url.port <= 65535
            valid = (
                url.scheme in {"http", "https"} and bool(url.hostname)
                and url.username is None and url.password is None
                and not url.query and not url.fragment and valid_port
                and not any(character.isspace() or ord(character) < 32 for character in value)
                and "\\" not in value
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Use an HTTP(S) API base URL without credentials, query, or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def require_backend_settings(self):
        if self.backend == "openai_compatible" and (not self.base_url or not self.model_alias):
            raise ValueError("An API base URL and model alias are required for an OpenAI-compatible backend")
        return self

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def reject_boolean_timeout(cls, value):
        if isinstance(value, bool):
            raise ValueError("Timeout must be a number")
        return value


def environment_model_settings(env: Mapping[str, str] | None = None) -> ModelSettings:
    source = os.environ if env is None else env
    if source.get("RESPAWNED_MODEL_BACKEND") == "codex_cli":
        return ModelSettings(backend="codex_cli", model_alias=source.get("RESPAWNED_CODEX_MODEL", ""),
                             timeout_seconds=source.get("RESPAWNED_CODEX_TIMEOUT_SECONDS", 120))
    return ModelSettings(
        backend=source.get("RESPAWNED_MODEL_BACKEND", "openai_compatible"),
        base_url=source.get("LITELLM_PROXY_URL", DEFAULT_PROXY_URL),
        model_alias=source.get("LITELLM_MODEL_ALIAS", DEFAULT_MODEL_ALIAS),
        timeout_seconds=source.get("LITELLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
    )


def load_model_settings(connection: Connection) -> ModelSettings | None:
    value = connection.execute(text(
        "SELECT value FROM application_settings WHERE key = 'drafting_model'"
    )).scalar_one_or_none()
    return ModelSettings.model_validate(value) if value is not None else None


def save_model_settings(connection: Connection, settings: ModelSettings) -> None:
    connection.execute(text("""
        INSERT INTO application_settings (key, value) VALUES ('drafting_model', CAST(:value AS jsonb))
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    """), {"value": settings.model_dump_json()})


def configured_adapter(connection: Connection) -> LiteLLMAdapter | CodexDraftingAdapter:
    """Use the database configuration when saved, otherwise existing env defaults.

    Secrets are resolved only on the server, at generation time. A saved backend
    with an absent credential fails instead of silently drafting elsewhere.
    """
    settings = load_model_settings(connection) or environment_model_settings()
    if settings.backend == "codex_cli":
        return CodexDraftingAdapter(model_alias=settings.model_alias,
                                    timeout_seconds=settings.timeout_seconds)
    return LiteLLMAdapter(
        proxy_url=settings.base_url, model_alias=settings.model_alias,
        timeout_seconds=settings.timeout_seconds,
        master_key=os.environ.get(settings.api_key_env, ""),
    )
