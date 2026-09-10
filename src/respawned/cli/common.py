"""Shared command-line configuration."""

import argparse
from datetime import UTC, datetime


from respawned.config import DEFAULT_POLICY_PATH


def parse_now(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--now must include a UTC offset")
    return parsed.astimezone(UTC)
