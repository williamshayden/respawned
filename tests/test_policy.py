from decimal import Decimal
from pathlib import Path

import pytest

from respawned.core.context import BusinessContext
from respawned.core.policy import load_policy


DEFAULT_POLICY_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "respawned"
    / "config"
    / "policy.yaml"
)


def _write_policy(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_default_policy_loads_generic_ranking_and_reasons():
    policy = load_policy(DEFAULT_POLICY_PATH)

    assert policy.business_context == BusinessContext("UTC")
    assert policy.cooldown_hours == Decimal("72")
    assert policy.dead_after_days == 45
    assert policy.ranking.value_weight == Decimal("20")
    assert policy.ranking.signal_weight == Decimal("20")
    assert policy.drafting.max_characters == 320
    assert policy.reasons["replied_no_answer"].evaluator == "reply_after_outbound"
    assert policy.reasons["replied_no_answer"].base_score == Decimal("60")
    assert policy.reasons["high_value_quiet"].params["high_percentile"] == 0.75


def test_custom_reason_and_params_require_no_loader_registration(tmp_path):
    path = _write_policy(
        tmp_path,
        """
version: 1
cooldown_hours: 0
dead_after_days: 1
ranking: {value_weight: 0, signal_weight: 0}
reasons:
  custom_reason:
    evaluator: custom_evaluator
    base_score: 12.5
    tone: direct
    params:
      arbitrary_threshold: 7
      nested: {enabled: true}
""",
    )

    reason = load_policy(path).reasons["custom_reason"]

    assert reason.evaluator == "custom_evaluator"
    assert reason.base_score == Decimal("12.5")
    assert reason.params == {
        "arbitrary_threshold": 7,
        "nested": {"enabled": True},
    }


@pytest.mark.parametrize(
    "fragment, message",
    [
        ("priority: 1", "unknown fields"),
        ("amount_weight: 1", "unknown fields"),
        ("recency_weight: 1", "unknown fields"),
        ("recency_days: 1", "unknown fields"),
    ],
)
def test_legacy_per_reason_ranking_fields_are_rejected(tmp_path, fragment, message):
    path = _write_policy(
        tmp_path,
        f"""
version: 1
cooldown_hours: 72
dead_after_days: 45
ranking: {{value_weight: 20, signal_weight: 20}}
reasons:
  example:
    evaluator: custom
    base_score: 10
    tone: concise
    {fragment}
""",
    )

    with pytest.raises(ValueError, match=message):
        load_policy(path)


@pytest.mark.parametrize(
    "body, message",
    [
        (
            """
version: 1
cooldown_hours: 72
dead_after_days: 45
ranking: {value_weight: 20}
reasons: {}
""",
            "ranking is missing.*signal_weight",
        ),
        (
            """
version: 1
cooldown_hours: 72
dead_after_days: 45
ranking: {value_weight: -1, signal_weight: 20}
reasons: {}
""",
            "ranking.value_weight must be non-negative",
        ),
        (
            """
version: 2
cooldown_hours: 72
dead_after_days: 45
ranking: {value_weight: 20, signal_weight: 20}
reasons: {}
""",
            "Unsupported policy version",
        ),
        (
            """
version: 1
cooldown_hours: 72
dead_after_days: 45
ranking: {value_weight: 20, signal_weight: 20}
reasons:
  example: {evaluator: custom, base_score: -1, tone: concise}
""",
            "reasons.example.base_score must be non-negative",
        ),
    ],
)
def test_invalid_structural_policy_values_fail_clearly(tmp_path, body, message):
    with pytest.raises(ValueError, match=message):
        load_policy(_write_policy(tmp_path, body))
