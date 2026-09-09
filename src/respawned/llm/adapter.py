"""Import-safe adapter for the application's LiteLLM proxy."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import os
from typing import Any


DEFAULT_PROXY_URL = "http://litellm:4000"
DEFAULT_MODEL_ALIAS = "respawned-default"

CompletionFunction = Callable[..., Any]
ChatMessage = Mapping[str, str]


class LLMAdapterError(RuntimeError):
    """Raised when the proxy response does not contain usable copy."""


def _default_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: Sequence[ChatMessage],
) -> Any:
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key)
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "proxy_url", self.proxy_url.rstrip("/"))

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
            model_alias=source.get(
                "LITELLM_MODEL_ALIAS",
                source.get("LLM_MODEL", DEFAULT_MODEL_ALIAS),
            ),
            completion_fn=completion_fn,
        )

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        """Return the assistant text from one proxy completion."""
        response = self.completion_fn(
            model=self.model_alias,
            messages=list(messages),
            base_url=self.proxy_url,
            api_key=self.master_key,
        )
        return _response_content(response)
