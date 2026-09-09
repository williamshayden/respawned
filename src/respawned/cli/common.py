"""Shared command-line configuration."""

import argparse
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_POLICY_PATH = Path(__file__).parents[1] / "config" / "policy.yaml"


def parse_now(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--now must include a UTC offset")
    return parsed.astimezone(UTC)
