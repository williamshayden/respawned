from dataclasses import FrozenInstanceError

import pytest

from respawned.core.draft import draft_follow_up
from respawned.core.helpers.payload import DraftPayload
from respawned.core.helpers.validate import DraftValidationError
from respawned.llm.adapter import LiteLLMAdapter


def _payload(**overrides):
    values = {
        "customer_name": "John",
        "tech_name": "Bob",
        "tone": "warm check-in",
        "other_open_quote_count": 1,
        "max_characters": 180,
        "sign_off": "Service Team",
    }
    values.update(overrides)
    return DraftPayload(**values)


def test_draft_payload_is_immutable():
    payload = _payload()

    with pytest.raises(FrozenInstanceError):
        payload.customer_name = "Jane"


def test_draft_follow_up_uses_safe_context_and_applies_sign_off():
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": "Hi John, just checking in on both quotes."
                    }
                }
            ]
        }

    adapter = LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=fake_completion,
    )

    body = draft_follow_up(_payload(), quote_status="open", adapter=adapter)

    assert body == (
        "Hi John, just checking in on both quotes.\n\nService Team"
    )
    prompt = captured["messages"][1]["content"]
    assert "John" in prompt
    assert "Bob" in prompt
    assert "warm check-in" in prompt
    assert "2 open quotes" in prompt
    assert "Service Team" in prompt


def test_draft_follow_up_blocks_terminal_quote_before_model_call():
    called = False

    def fake_completion(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("terminal quote must not call the model")

    adapter = LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=fake_completion,
    )

    with pytest.raises(DraftValidationError, match="terminal"):
        draft_follow_up(_payload(), quote_status="dismissed", adapter=adapter)

    assert called is False


def test_draft_follow_up_validates_generated_copy():
    def fake_completion(**_kwargs):
        return {"choices": [{"message": {"content": "Your quote is $500."}}]}

    adapter = LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=fake_completion,
    )

    with pytest.raises(DraftValidationError, match="currency"):
        draft_follow_up(_payload(), quote_status="open", adapter=adapter)
