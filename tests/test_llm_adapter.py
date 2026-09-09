import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from respawned.llm.adapter import (
    DEFAULT_MODEL_ALIAS,
    DEFAULT_TIMEOUT_SECONDS,
    LLMAdapterError,
    LiteLLMAdapter,
)


def test_adapter_sends_unprefixed_alias_to_proxy_request():
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Hi John, just checking in.")
                )
            ]
        )

    adapter = LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=fake_completion,
    )
    messages = [{"role": "user", "content": "Draft a follow-up."}]

    assert adapter.complete(messages) == "Hi John, just checking in."
    assert captured == {
        "model": "follow-up-model",
        "messages": messages,
        "base_url": "http://proxy.test:4000",
        "api_key": "sk-test",
    }


@pytest.mark.parametrize("provider_fails", (False, True))
def test_default_request_uses_and_closes_openai_client_against_proxy(
    monkeypatch, provider_fails
):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            if provider_fails:
                raise RuntimeError("provider unavailable")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Hi John, just checking in."
                        )
                    )
                ]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self):
            captured["closed"] = True

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(
            completion=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("legacy LiteLLM SDK request path was used")
            )
        ),
    )
    adapter = LiteLLMAdapter(
        proxy_url="http://proxy.test:4000/",
        master_key="sk-test",
        model_alias="follow-up-model",
    )
    messages = [{"role": "user", "content": "Draft a follow-up."}]

    if provider_fails:
        with pytest.raises(LLMAdapterError, match="completion failed") as error:
            adapter.complete(messages)
        assert isinstance(error.value.__cause__, RuntimeError)
    else:
        assert adapter.complete(messages) == "Hi John, just checking in."
    assert captured == {
        "closed": True,
        "client": {
            "base_url": "http://proxy.test:4000",
            "api_key": "sk-test",
            "timeout": DEFAULT_TIMEOUT_SECONDS,
            "max_retries": 0,
        },
        "request": {
            "model": "follow-up-model",
            "messages": messages,
        },
    }


def test_adapter_extracts_mapping_content():
    def fake_completion(**_kwargs):
        return {
            "choices": [
                {"message": {"content": "Hi John, how can we help?"}}
            ]
        }

    adapter = LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=fake_completion,
    )

    assert adapter.complete([]) == "Hi John, how can we help?"


def test_adapter_reads_proxy_configuration_from_environment_mapping():
    def fake_completion(**_kwargs):
        raise AssertionError("configuration must not call the model")

    adapter = LiteLLMAdapter.from_env(
        {
            "LITELLM_PROXY_URL": "http://custom-proxy:4000/",
            "LITELLM_MASTER_KEY": "sk-custom",
            "LITELLM_MODEL_ALIAS": "custom-model",
            "LITELLM_TIMEOUT_SECONDS": "12.5",
        },
        completion_fn=fake_completion,
    )

    assert adapter.proxy_url == "http://custom-proxy:4000"
    assert adapter.master_key == "sk-custom"
    assert adapter.model_alias == "custom-model"
    assert adapter.timeout_seconds == 12.5


def test_adapter_uses_stable_default_alias_independent_of_upstream_provider():
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [{"message": {"content": "Provider-neutral reply"}}]
        }

    adapter = LiteLLMAdapter.from_env(
        {
            "LITELLM_MASTER_KEY": "sk-proxy",
            "LITELLM_UPSTREAM_MODEL": "openrouter/example/model",
            "LITELLM_UPSTREAM_API_KEY": "upstream-secret",
        },
        completion_fn=fake_completion,
    )

    assert DEFAULT_MODEL_ALIAS == "respawned-default"
    assert adapter.complete([]) == "Provider-neutral reply"
    assert captured["model"] == "respawned-default"
    assert captured["api_key"] == "sk-proxy"


def test_adapter_wraps_proxy_or_provider_failures():
    def unavailable(**_kwargs):
        raise RuntimeError("provider is unavailable")

    adapter = LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=unavailable,
    )

    with pytest.raises(LLMAdapterError, match="completion failed") as error:
        adapter.complete([])

    assert isinstance(error.value.__cause__, RuntimeError)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("proxy_url", " ", "LITELLM_PROXY_URL"),
        ("master_key", " ", "LITELLM_MASTER_KEY"),
        ("model_alias", " ", "LITELLM_MODEL_ALIAS"),
    ),
)
def test_adapter_rejects_blank_configuration(field, value, message):
    values = {
        "proxy_url": "http://proxy.test:4000",
        "master_key": "sk-test",
        "model_alias": "follow-up-model",
    }
    values[field] = value

    with pytest.raises(ValueError, match=message):
        LiteLLMAdapter(**values)


@pytest.mark.parametrize("value", [0, -1, 301, "nan", "inf", "-inf", "bad", "", True])
def test_adapter_rejects_unbounded_or_invalid_timeout(value):
    with pytest.raises(ValueError, match="LITELLM_TIMEOUT_SECONDS"):
        LiteLLMAdapter("http://unused.test", "unused", "scripted", timeout_seconds=value)


def test_stalled_local_proxy_times_out_without_retrying_or_exposing_provider_details():
    """Exercise the real SDK against a local socket; no provider is contacted."""
    from openai import APITimeoutError

    release = Event()
    received = Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(self.path)
            received.set()
            release.wait(5)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    adapter = LiteLLMAdapter(
        f"http://127.0.0.1:{server.server_port}", "private-test-credential", "unused",
        timeout_seconds=0.1,
    )
    try:
        with pytest.raises(LLMAdapterError, match="LiteLLM completion failed") as error:
            adapter.complete([{"role": "user", "content": "Synthetic timeout probe"}])
        assert received.wait(1)
        assert isinstance(error.value.__cause__, APITimeoutError)
        assert "private-test-credential" not in str(error.value)
        assert requests == ["/chat/completions"]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
