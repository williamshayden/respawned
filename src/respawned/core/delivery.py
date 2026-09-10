"""Read authorized messages and record confirmed results from an external sender."""

from datetime import datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.core.contact import lock_contact_keys
from respawned.core.contracts import OutboxReceiptIn
from respawned.core.ingest import IngestConflictError, ingest_records
from respawned.core.outbox import CSV_FIELDS
from respawned.core.time import aware_utc


class OutboxNotFoundError(LookupError):
    """The requested reservation does not exist."""


class OutboxReceiptConflictError(RuntimeError):
    """The result conflicts with recorded evidence or authorization provenance."""


_OUTBOX_COLUMNS = ", ".join(f"outbox.{field}" for field in CSV_FIELDS)


def list_pending_outbox(connection: Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    """Read a bounded batch, without claims or a durable synchronization cursor.

    Call again from the beginning after recording results. Identity allocation
    does not order transaction commits; a high-water ID can skip a late commit.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 201:
        raise ValueError("limit must be between 1 and 201")
    return [dict(row) for row in connection.execute(text(f"""
        SELECT {_OUTBOX_COLUMNS} FROM outbox
        WHERE status = 'pending' AND authorization_mode IN ('human', 'automatic')
        ORDER BY id LIMIT :limit
    """), {"limit": limit}).mappings()]


def get_outbox_item(
    connection: Connection, outbox_id: int, *, for_update: bool = False,
) -> dict[str, Any]:
    if (isinstance(outbox_id, bool) or not isinstance(outbox_id, int)
            or not 1 <= outbox_id <= 9223372036854775807):
        raise ValueError("outbox_id must be a positive PostgreSQL BIGINT")
    row = connection.execute(text(f"""
        SELECT {_OUTBOX_COLUMNS}, r.sender AS receipt_sender,
               r.provider_message_id AS receipt_provider_message_id,
               r.sent_at AS receipt_sent_at, r.recorded_at AS receipt_recorded_at
        FROM outbox LEFT JOIN outbox_receipts r ON r.outbox_id = outbox.id
        WHERE outbox.id = :id
    """ + (" FOR UPDATE OF outbox" if for_update else "")),
        {"id": outbox_id}).mappings().one_or_none()
    if row is None:
        raise OutboxNotFoundError("Outbox item not found")
    return {
        **{field: row[field] for field in CSV_FIELDS},
        "receipt": ({
            field: row[f"receipt_{field}"]
            for field in ("sender", "provider_message_id", "sent_at", "recorded_at")
        } if row["receipt_sender"] is not None else None),
    }


def receipt_activity_id(draft_id: object, opportunity_id: str) -> str:
    """One stable event per approved draft and grouped record, across retries."""
    digest = sha256(opportunity_id.encode("utf-8")).hexdigest()
    return f"respawned:outbox:{draft_id}:{digest}:sent"


def record_outbox_receipt(
    connection: Connection, *, outbox_id: int, receipt: OutboxReceiptIn, now: datetime,
) -> dict[str, Any]:
    """Atomically correlate a confirmed send and append its outbound evidence.

    The caller commits before reporting success. A savepoint also prevents
    partial writes if an in-process caller catches a conflict and continues.
    This never calls a provider or changes the reviewed recipient and copy.
    """
    now = aware_utc(now, "now")
    sent_at = aware_utc(receipt.sent_at, "sent_at")
    values = {**receipt.model_dump(), "sent_at": sent_at}
    with connection.begin_nested():
        initial = get_outbox_item(connection, outbox_id)
        # Match ingestion/review ordering: opportunity rows, contact advisory
        # locks, then the outbox row. Current contacts may differ from approval;
        # lock both identities without replacing the immutable approved route.
        contacts = connection.execute(text("""
            SELECT id, contact_key FROM opportunities
            WHERE id = ANY(:ids) ORDER BY id FOR UPDATE
        """), {"ids": initial["opportunity_ids"]}).mappings().all()
        if {row["id"] for row in contacts} != set(initial["opportunity_ids"]):
            raise OutboxReceiptConflictError("An approved record no longer exists")
        lock_contact_keys(connection, [initial["contact_key"], *(row["contact_key"] for row in contacts)])
        item = get_outbox_item(connection, outbox_id, for_update=True)
        if item["receipt"] is not None:
            if all(item["receipt"][key] == value for key, value in values.items()):
                return item
            raise OutboxReceiptConflictError("A different receipt is already recorded for this outbox item")
        if sent_at < item["created_at"]:
            raise ValueError("sent_at cannot precede the outbox reservation")
        if sent_at > now:
            raise ValueError("sent_at cannot be in the future")
        if item["authorization_mode"] == "legacy_unknown":
            raise OutboxReceiptConflictError("This reservation has no recorded human or automatic authorization")
        if item["status"] != "pending":
            raise OutboxReceiptConflictError("Only pending outbox items can receive a new receipt")

        inserted = connection.execute(text("""
            INSERT INTO outbox_receipts (outbox_id, sender, provider_message_id, sent_at, recorded_at)
            VALUES (:id, :sender, :provider_message_id, :sent_at, :now)
            ON CONFLICT DO NOTHING RETURNING outbox_id
        """), {"id": outbox_id, **values, "now": now}).scalar_one_or_none()
        if inserted is None:
            raise OutboxReceiptConflictError("This sender message ID already belongs to another outbox item")
        try:
            ingest_records(connection, activities=[{
                "id": receipt_activity_id(item["draft_id"], opportunity_id),
                "opportunity_id": opportunity_id,
                "type": "message_sent", "direction": "outbound", "channel": item["channel"],
                "occurred_at": sent_at, "classification": "unknown",
            } for opportunity_id in item["opportunity_ids"]])
        except IngestConflictError as exc:
            raise OutboxReceiptConflictError("Outbound evidence conflicts with this receipt") from exc
        connection.execute(text("""
            UPDATE outbox SET status = 'sent', sent_at = :sent_at WHERE id = :id
        """), {"id": outbox_id, "sent_at": sent_at})
        return get_outbox_item(connection, outbox_id)
