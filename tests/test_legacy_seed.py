from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path

import pytest

from respawned.adapters.legacy_seed import (
    LegacySeedError,
    load_legacy_seed,
)
from respawned.core.ingest import IngestConflictError, ingest_records


def _quote(quote_id: str = "Q-1", **overrides):
    record = {
        "id": quote_id,
        "customer_name": "Avery Example",
        "customer_phone": "+1 (212) 555-0100",
        "tech_name": "Morgan Owner",
        "amount": 1234.5,
        "status": "open",
        "created_at": "2026-08-01T12:00:00Z",
        "last_contact_at": "2026-08-02T12:00:00Z",
    }
    record.update(overrides)
    return record


def _event(event_id: str, event_type: str, **overrides):
    record = {
        "event_id": event_id,
        "type": event_type,
        "quote_id": "Q-1",
        "timestamp": "2026-08-03T12:00:00Z",
    }
    record.update(overrides)
    return record


def _write_seed(tmp_path, quotes, events):
    quotes_path = tmp_path / "quotes.json"
    events_path = tmp_path / "events.jsonl"
    quotes_path.write_text(json.dumps(quotes), encoding="utf-8")
    events_path.write_text(
        "\n".join(json.dumps(event) for event in events),
        encoding="utf-8",
    )
    return quotes_path, events_path


def test_maps_legacy_quotes_and_every_canonical_activity_type(tmp_path):
    quotes = [
        _quote("Q-1"),
        _quote("Q-2", status="accepted"),
        _quote("Q-3", status="dismissed"),
    ]
    events = [
        _event("E-1", "quote_sent"),
        _event("E-2", "quote_viewed"),
        _event(
            "E-3",
            "customer_replied",
            channel="email",
            direction="inbound",
        ),
        _event(
            "E-4",
            "message_sent",
            channel="sms",
            direction="outbound",
        ),
        _event("E-5", "quote_accepted"),
        _event("E-6", "quote_dismissed"),
    ]
    batch = load_legacy_seed(*_write_seed(tmp_path, quotes, events))

    assert [record["status"] for record in batch.opportunities] == [
        "open",
        "won",
        "lost",
    ]
    assert batch.opportunities[0] == {
        "id": "Q-1",
        "contact_key": "phone:12125550100",
        "contact_name": "Avery Example",
        "contact_phone": "+1 (212) 555-0100",
        "contact_email": None,
        "owner_name": "Morgan Owner",
        "value": Decimal("1234.5"),
        "status": "open",
        "created_at": datetime(2026, 8, 1, 12, tzinfo=UTC),
        "last_contact_at": datetime(2026, 8, 2, 12, tzinfo=UTC),
        "preferred_channel": None,
    }
    assert [record["type"] for record in batch.activities] == [
        "opportunity_created",
        "content_viewed",
        "contact_replied",
        "message_sent",
        "opportunity_won",
        "opportunity_lost",
    ]
    assert batch.activities[2]["channel"] == "email"
    assert batch.activities[2]["direction"] == "inbound"
    assert batch.activities[3]["channel"] == "sms"
    assert batch.activities[3]["direction"] == "outbound"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "", "quote[0].id is required"),
        ("customer_phone", None, "quote[0].customer_phone is required"),
        (
            "created_at",
            None,
            "quote[0].created_at must be a timezone-aware timestamp",
        ),
    ],
)
def test_rejects_quotes_without_minimum_ingest_fields(
    tmp_path, field, value, message
):
    paths = _write_seed(tmp_path, [_quote(**{field: value})], [])

    with pytest.raises(LegacySeedError, match=message.replace("[", r"\[")):
        load_legacy_seed(*paths)


def test_requires_activity_id_and_known_opportunity(tmp_path):
    missing_id = _event("", "quote_viewed")
    paths = _write_seed(tmp_path, [_quote()], [missing_id])
    with pytest.raises(LegacySeedError, match=r"event\[0\].event_id is required"):
        load_legacy_seed(*paths)

    unknown = _event("E-1", "quote_viewed", quote_id="Q-missing")
    paths = _write_seed(tmp_path, [_quote()], [unknown])
    with pytest.raises(LegacySeedError, match="references unknown opportunity"):
        load_legacy_seed(*paths)


def test_preserves_duplicate_ids_for_canonical_ingest_validation(tmp_path):
    first = _event("E-duplicate", "quote_viewed")
    exact_duplicate = dict(first)
    conflicting = _event("E-duplicate", "customer_replied")

    exact_batch = load_legacy_seed(
        *_write_seed(tmp_path, [_quote()], [first, exact_duplicate])
    )
    assert len(exact_batch.activities) == 2
    assert exact_batch.activities[0] == exact_batch.activities[1]

    conflict_batch = load_legacy_seed(
        *_write_seed(tmp_path, [_quote()], [first, conflicting])
    )
    with pytest.raises(IngestConflictError, match="conflicting activity payloads"):
        ingest_records(object(), activities=conflict_batch.activities)


def test_bundled_demo_seed_maps_without_database():
    from respawned.__main__ import DEFAULT_SEED_DIR

    batch = load_legacy_seed(
        DEFAULT_SEED_DIR / "quotes.json",
        DEFAULT_SEED_DIR / "events.jsonl",
    )

    assert len(batch.opportunities) == 30
    assert len(batch.activities) == 88
    assert len({activity["id"] for activity in batch.activities}) == 82
