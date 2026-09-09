"""Explicit demo adapter for the repository's original quote/event fixtures."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any


STATUS_MAP = {"open": "open", "accepted": "won", "dismissed": "lost"}
ACTIVITY_TYPE_MAP = {
    "quote_sent": "opportunity_created",
    "quote_viewed": "content_viewed",
    "customer_replied": "contact_replied",
    "message_sent": "message_sent",
    "quote_accepted": "opportunity_won",
    "quote_dismissed": "opportunity_lost",
}


class LegacySeedError(ValueError):
    """A legacy fixture cannot be represented by the canonical ingest model."""


@dataclass(frozen=True, slots=True)
class LegacySeedBatch:
    opportunities: tuple[dict[str, Any], ...]
    activities: tuple[dict[str, Any], ...]


def _required_text(record: dict[str, Any], key: str, label: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LegacySeedError(f"{label}.{key} is required")
    return value.strip()


def _optional_text(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise LegacySeedError(f"{key} must be text when provided")
    return value.strip() or None


def _timestamp(record: dict[str, Any], key: str, label: str) -> datetime:
    value = record.get(key)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LegacySeedError(f"{label}.{key} must be an ISO timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LegacySeedError(f"{label}.{key} must be a timezone-aware timestamp")
    return value.astimezone(UTC)


def _optional_timestamp(
    record: dict[str, Any], key: str, label: str
) -> datetime | None:
    return None if record.get(key) is None else _timestamp(record, key, label)


def _value(record: dict[str, Any], label: str) -> Decimal | None:
    raw = record.get("amount")
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise LegacySeedError(f"{label}.amount must be numeric") from exc
    if not value.is_finite() or value < 0:
        raise LegacySeedError(f"{label}.amount must be a non-negative number")
    return value


def _choice(
    record: dict[str, Any], key: str, choices: set[str], label: str
) -> str | None:
    value = _optional_text(record, key)
    if value is not None and value not in choices:
        expected = ", ".join(sorted(choices))
        raise LegacySeedError(f"{label}.{key} must be one of: {expected}")
    return value


def _map_quote(record: dict[str, Any], label: str) -> dict[str, Any]:
    opportunity_id = _required_text(record, "id", label)
    phone = _required_text(record, "customer_phone", label)
    digits = "".join(character for character in phone if character.isdigit())
    if not digits:
        raise LegacySeedError(
            f"{label}.customer_phone must contain a contact destination"
        )
    legacy_status = _required_text(record, "status", label).lower()
    try:
        status = STATUS_MAP[legacy_status]
    except KeyError as exc:
        expected = ", ".join(STATUS_MAP)
        raise LegacySeedError(f"{label}.status must be one of: {expected}") from exc

    return {
        "id": opportunity_id,
        "contact_key": f"phone:{digits}",
        "contact_name": _optional_text(record, "customer_name"),
        "contact_phone": phone,
        "contact_email": None,
        "owner_name": _optional_text(record, "tech_name"),
        "value": _value(record, label),
        "status": status,
        "created_at": _timestamp(record, "created_at", label),
        "last_contact_at": _optional_timestamp(
            record, "last_contact_at", label
        ),
        "preferred_channel": None,
    }


def _map_event(record: dict[str, Any], label: str) -> dict[str, Any]:
    legacy_type = _required_text(record, "type", label)
    try:
        activity_type = ACTIVITY_TYPE_MAP[legacy_type]
    except KeyError as exc:
        raise LegacySeedError(
            f"{label}.type is unsupported by the legacy adapter: {legacy_type!r}"
        ) from exc
    return {
        "id": _required_text(record, "event_id", label),
        "type": activity_type,
        "opportunity_id": _required_text(record, "quote_id", label),
        "occurred_at": _timestamp(record, "timestamp", label),
        "channel": _choice(record, "channel", {"email", "sms"}, label),
        "direction": _choice(
            record, "direction", {"inbound", "outbound"}, label
        ),
    }


def _read_quotes(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LegacySeedError(f"could not read legacy quotes from {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise LegacySeedError(f"legacy quotes file must contain a JSON list: {path}")
    if not all(isinstance(record, dict) for record in payload):
        raise LegacySeedError(f"legacy quotes must all be JSON objects: {path}")
    return payload


def _read_events(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LegacySeedError(f"could not read legacy events from {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LegacySeedError(
                f"invalid JSON in legacy events at {path}:{line_number}"
            ) from exc
        if not isinstance(record, dict):
            raise LegacySeedError(
                f"legacy event at {path}:{line_number} must be a JSON object"
            )
        records.append(record)
    return records


def load_legacy_seed(
    quotes_path: str | Path,
    events_path: str | Path,
) -> LegacySeedBatch:
    """Map the old demo fixture shape to canonical ingest dictionaries."""
    quote_records = _read_quotes(Path(quotes_path))
    event_records = _read_events(Path(events_path))
    opportunities = tuple(
        _map_quote(record, f"quote[{index}]")
        for index, record in enumerate(quote_records)
    )
    opportunity_ids = {record["id"] for record in opportunities}
    activities = tuple(
        _map_event(record, f"event[{index}]")
        for index, record in enumerate(event_records)
    )
    for index, activity in enumerate(activities):
        if activity["opportunity_id"] not in opportunity_ids:
            raise LegacySeedError(
                f"event[{index}].quote_id references unknown opportunity "
                f"{activity['opportunity_id']!r}"
            )
    return LegacySeedBatch(opportunities, activities)
