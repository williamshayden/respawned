"""Import-safe adapter for the application's LiteLLM proxy."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import math
import os
from typing import Any, Protocol


DEFAULT_PROXY_URL = "http://litellm:4000"
DEFAULT_MODEL_ALIAS = "respawned-default"
DEFAULT_TIMEOUT_SECONDS = 60.0

CompletionFunction = Callable[..., Any]
ChatMessage = Mapping[str, str]


class DraftingAdapter(Protocol):
    """Provider-independent text generation; the core owns validation and review."""

    def complete(self, messages: Sequence[ChatMessage]) -> str: ...


class LLMAdapterError(RuntimeError):
    """Raised when the proxy response does not contain usable copy."""


def _default_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: Sequence[ChatMessage],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    from openai import OpenAI

    with OpenAI(
        base_url=base_url, api_key=api_key,
        timeout=timeout_seconds, max_retries=0,
    ) as client:
        return client.chat.completions.create(
            model=model,
            messages=list(messages),
        )


def _response_content(response: Any) -> str:
    try:
        if isinstance(response, Mapping):
            content = response["choices"][0]["message"]["content"]
        else:
            content = response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise LLMAdapterError("LiteLLM response did not contain message content") from exc

    if not isinstance(content, str):
        raise LLMAdapterError("LiteLLM response content must be text")
    return content


@dataclass(frozen=True, slots=True)
class LiteLLMAdapter:
    """Send chat-completion requests through a configurable LiteLLM proxy."""

    proxy_url: str
    master_key: str = field(repr=False)
    model_alias: str
    completion_fn: CompletionFunction = field(
        default=_default_completion,
        repr=False,
        compare=False,
    )
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        proxy_url = self.proxy_url.strip().rstrip("/")
        master_key = self.master_key.strip()
        model_alias = self.model_alias.strip()
        if not proxy_url:
            raise ValueError("LITELLM_PROXY_URL is required")
        if not master_key:
            raise ValueError("LITELLM_MASTER_KEY is required")
        if not model_alias:
            raise ValueError("LITELLM_MODEL_ALIAS is required")
        timeout_error = (
            "LITELLM_TIMEOUT_SECONDS must be a finite number "
            "greater than 0 and at most 300"
        )
        try:
            timeout_seconds = float(self.timeout_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(timeout_error) from exc
        if (
            isinstance(self.timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 300
        ):
            raise ValueError(timeout_error)
        object.__setattr__(self, "proxy_url", proxy_url)
        object.__setattr__(self, "master_key", master_key)
        object.__setattr__(self, "model_alias", model_alias)
        object.__setattr__(self, "timeout_seconds", timeout_seconds)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        completion_fn: CompletionFunction = _default_completion,
    ) -> "LiteLLMAdapter":
        """Build an adapter without reading environment state at import time."""
        source = os.environ if env is None else env
        master_key = source.get("LITELLM_MASTER_KEY", "").strip()
        if not master_key:
            raise ValueError("LITELLM_MASTER_KEY is required")

        return cls(
            proxy_url=source.get("LITELLM_PROXY_URL", DEFAULT_PROXY_URL),
            master_key=master_key,
            model_alias=source.get("LITELLM_MODEL_ALIAS", DEFAULT_MODEL_ALIAS),
            completion_fn=completion_fn,
            timeout_seconds=source.get(
                "LITELLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
            ),
        )

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        """Return assistant text; the SDK uses bounded operations without retries.

        A custom completion function owns its timeout, including simulation
        subprocesses. Its existing four-argument contract is unchanged.
        """
        try:
            arguments = {
                "model": self.model_alias, "messages": list(messages),
                "base_url": self.proxy_url, "api_key": self.master_key,
            }
            if self.completion_fn is _default_completion:
                arguments["timeout_seconds"] = self.timeout_seconds
            response = self.completion_fn(**arguments)
        except LLMAdapterError:
            raise
        except Exception as exc:
            raise LLMAdapterError("LiteLLM completion failed") from exc
        return _response_content(response)
