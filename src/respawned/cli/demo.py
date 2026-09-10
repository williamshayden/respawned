"""Import the bundled sample fixtures through the engine API."""
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

from respawned.adapters import load_legacy_seed
from respawned.client import APIError, RespawnedClient


def load_demo(seed_dir: Path, client: RespawnedClient) -> dict:
    try:
        batch = load_legacy_seed(seed_dir / "quotes.json", seed_dir / "events.jsonl")
    except ValueError as exc:
        raise APIError(str(exc)) from exc

    def encode(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        raise TypeError("Unsupported fixture value")

    payload = json.loads(json.dumps({
        "opportunities": batch.opportunities, "activities": batch.activities,
    }, default=encode))
    return client.import_records(payload)