import sys
from types import SimpleNamespace

from follow_up_engine.llm.adapter import LiteLLMAdapter


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


def test_default_request_uses_openai_client_against_proxy(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
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

    assert adapter.complete(messages) == "Hi John, just checking in."
    assert captured == {
        "client": {
            "base_url": "http://proxy.test:4000",
            "api_key": "sk-test",
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
        },
        completion_fn=fake_completion,
    )

    assert adapter.proxy_url == "http://custom-proxy:4000"
    assert adapter.master_key == "sk-custom"
    assert adapter.model_alias == "custom-model"
