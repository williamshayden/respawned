"""Validated configuration for follow-up eligibility, ranking, and drafting."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal, Mapping

import yaml

from respawned.core.configuration import (
    integer,
    mapping,
    nonempty_text,
    number,
    strict_mapping,
)
from respawned.core.context import BusinessContext

DEFAULT_SIGN_OFF = "Follow-up Team"


@dataclass(frozen=True, slots=True)
class RankingPolicy:
    value_weight: Decimal
    signal_weight: Decimal


@dataclass(frozen=True, slots=True)
class ReasonPolicy:
    evaluator: str
    base_score: Decimal
    tone: str
    params: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DraftingPolicy:
    sign_off: str = DEFAULT_SIGN_OFF
    max_characters: int = 320
    require_owner_name: bool = False


@dataclass(frozen=True, slots=True)
class ReviewPolicy:
    """Operator-controlled authorization mode; never supplied by source data."""

    mode: Literal["human", "automatic"] = "human"

    def __post_init__(self) -> None:
        if self.mode not in ("human", "automatic"):
            raise ValueError("review.mode must be 'human' or 'automatic'")


@dataclass(frozen=True, slots=True)
class Policy:
    business_context: BusinessContext
    cooldown_hours: Decimal
    dead_after_days: int
    ranking: RankingPolicy
    reasons: Mapping[str, ReasonPolicy]
    drafting: DraftingPolicy = DraftingPolicy()
    review: ReviewPolicy = ReviewPolicy()


def load_policy(path: str | Path) -> Policy:
    """Load a version-one policy without constraining evaluator-specific params."""
    policy_path = Path(path)
    try:
        loaded = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Unable to load policy {policy_path}") from exc

    raw = strict_mapping(
        loaded,
        f"Policy {policy_path}",
        {"version", "cooldown_hours", "dead_after_days", "ranking", "reasons"},
        {"business_timezone", "drafting", "review"},
    )
    if integer(raw["version"], "version", positive=True) != 1:
        raise ValueError(f"Unsupported policy version {raw['version']!r}")

    ranking_raw = strict_mapping(
        raw["ranking"],
        "ranking",
        {"value_weight", "signal_weight"},
    )
    ranking = RankingPolicy(
        value_weight=number(ranking_raw["value_weight"], "ranking.value_weight"),
        signal_weight=number(ranking_raw["signal_weight"], "ranking.signal_weight"),
    )

    reasons: dict[str, ReasonPolicy] = {}
    for name, value in mapping(raw["reasons"], "reasons").items():
        reason_name = nonempty_text(name, "reason name")
        if name != reason_name:
            raise ValueError(
                f"Reason name {name!r} must not have surrounding whitespace"
            )
        reason_raw = strict_mapping(
            value,
            f"reasons.{reason_name}",
            {"evaluator", "base_score", "tone"},
            {"params"},
        )
        params = mapping(reason_raw.get("params", {}), f"reasons.{reason_name}.params")
        if any(
            not isinstance(key, str) or not key.strip() or key != key.strip()
            for key in params
        ):
            raise ValueError(
                f"reasons.{reason_name}.params keys must be normalized non-empty text"
            )
        reasons[reason_name] = ReasonPolicy(
            evaluator=nonempty_text(
                reason_raw["evaluator"], f"reasons.{reason_name}.evaluator"
            ),
            base_score=number(
                reason_raw["base_score"], f"reasons.{reason_name}.base_score"
            ),
            tone=nonempty_text(reason_raw["tone"], f"reasons.{reason_name}.tone"),
            params=dict(params),
        )

    drafting_raw = strict_mapping(
        raw.get("drafting", {}),
        "drafting",
        set(),
        {"sign_off", "max_characters", "require_owner_name"},
    )
    require_owner_name = drafting_raw.get("require_owner_name", False)
    if not isinstance(require_owner_name, bool):
        raise ValueError("drafting.require_owner_name must be a boolean")
    drafting = DraftingPolicy(
        sign_off=nonempty_text(
            drafting_raw.get("sign_off", DEFAULT_SIGN_OFF), "drafting.sign_off"
        ),
        max_characters=integer(
            drafting_raw.get("max_characters", 320),
            "drafting.max_characters",
            positive=True,
        ),
        require_owner_name=require_owner_name,
    )

    review_raw = strict_mapping(raw.get("review", {}), "review", set(), {"mode"})
    review = ReviewPolicy(mode=review_raw.get("mode", "human"))

    return Policy(
        business_context=BusinessContext(
            nonempty_text(raw.get("business_timezone", "UTC"), "business_timezone")
        ),
        cooldown_hours=number(raw["cooldown_hours"], "cooldown_hours"),
        dead_after_days=integer(
            raw["dead_after_days"], "dead_after_days", positive=True
        ),
        ranking=ranking,
        reasons=reasons,
        drafting=drafting,
        review=review,
    )
