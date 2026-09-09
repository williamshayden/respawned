from dataclasses import FrozenInstanceError

import pytest

from respawned.core.draft import draft_follow_up
from respawned.core.helpers.payload import DraftPayload
from respawned.core.helpers.validate import DraftValidationError
from respawned.llm.adapter import LiteLLMAdapter


def _payload(**overrides):
    values = {
        "contact_name": "John",
        "owner_name": "Bob",
        "tone": "warm check-in",
        "other_open_opportunity_count": 1,
        "max_characters": 180,
        "sign_off": "Follow-up Team",
    }
    values.update(overrides)
    return DraftPayload(**values)


def test_draft_payload_is_immutable():
    payload = _payload()

    with pytest.raises(FrozenInstanceError):
        payload.contact_name = "Jane"


def test_draft_follow_up_uses_safe_context_and_applies_sign_off():
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": "Hi John, just checking in on both opportunities."
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

    body = draft_follow_up(_payload(), opportunity_status="open", adapter=adapter)

    assert body == (
        "Hi John, just checking in on both opportunities.\n\nFollow-up Team"
    )
    prompt = captured["messages"][1]["content"]
    assert "John" in prompt
    assert "Bob" in prompt
    assert "warm check-in" in prompt
    assert "2 open opportunities" in prompt
    assert "Follow-up Team" in prompt


def test_draft_follow_up_blocks_closed_opportunity_before_model_call():
    called = False

    def fake_completion(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("closed opportunity must not call the model")

    adapter = LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=fake_completion,
    )

    with pytest.raises(DraftValidationError, match="opportunity status"):
        draft_follow_up(_payload(), opportunity_status="lost", adapter=adapter)

    assert called is False


def test_draft_follow_up_validates_generated_copy():
    def fake_completion(**_kwargs):
        return {"choices": [{"message": {"content": "The value is $500."}}]}

    adapter = LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=fake_completion,
    )

    with pytest.raises(DraftValidationError, match="currency"):
        draft_follow_up(_payload(), opportunity_status="open", adapter=adapter)
