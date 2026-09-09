from pathlib import Path

import pytest

from respawned.core.policy import ReviewPolicy, load_policy


BASE_POLICY = """
version: 1
cooldown_hours: 72
dead_after_days: 45
ranking: {value_weight: 20, signal_weight: 20}
reasons: {}
"""


def _load(tmp_path: Path, review: str = ""):
    path = tmp_path / "policy.yaml"
    path.write_text(BASE_POLICY + review, encoding="utf-8")
    return load_policy(path)


def test_existing_policy_defaults_to_human_review(tmp_path):
    assert _load(tmp_path).review == ReviewPolicy(mode="human")


@pytest.mark.parametrize("mode", ("human", "automatic"))
def test_operator_can_select_explicit_review_mode(tmp_path, mode):
    assert _load(tmp_path, f"review: {{mode: {mode}}}").review.mode == mode


@pytest.mark.parametrize(
    "review",
    (
        "review: {mode: auto}",
        "review: {mode: false}",
        "review: {mode: null}",
        "review: {mode: []}",
        "review: {mode: ' automatic '}",
        "review: {mode: automatic, skip_validation: true}",
        "review: automatic",
    ),
)
def test_invalid_review_configuration_fails_closed(tmp_path, review):
    with pytest.raises(ValueError, match="review"):
        _load(tmp_path, review)


def test_programmatic_review_policy_rejects_invalid_mode():
    with pytest.raises(ValueError, match="review.mode"):
        ReviewPolicy(mode="disabled")
