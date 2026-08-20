from types import SimpleNamespace

from follow_up_engine.llm.adapter import LiteLLMAdapter


def test_adapter_addresses_proxy_alias_through_openai_compatible_client():
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
        "model": "openai/follow-up-model",
        "messages": messages,
        "api_base": "http://proxy.test:4000",
        "api_key": "sk-test",
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
