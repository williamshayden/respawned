"""Read-only outbox snapshots and exports shared by CLI and HTTP adapters."""

import csv
import io
import json
from collections.abc import Iterable, Mapping
from datetime import UTC
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


CSV_FIELDS = (
    "id", "draft_id", "contact_key", "contact_address", "contact_name",
    "channel", "opportunity_ids", "body", "status", "authorization_mode",
    "created_at", "sent_at",
)


def list_outbox_rows(
    connection: Connection, *, limit: int | None = None, offset: int = 0,
    newest_first: bool = False, kinds: Iterable[str] | None = None,
) -> list[Mapping[str, Any]]:
    """Read a stable-ID ordered snapshot without claiming or acknowledging delivery.

    A workspace selects whole reservations containing a matching record kind;
    it never truncates the approved recipient, body, or grouped record IDs.
    Pagination is a current snapshot, not a delivery synchronization checkpoint.
    """
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValueError("limit must be a positive integer")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    query = f"SELECT {', '.join(CSV_FIELDS)} FROM outbox"
    parameters: dict[str, Any] = {}
    selected_kinds = sorted(set(kinds or ()))
    if selected_kinds:
        query += """ WHERE EXISTS (
            SELECT 1 FROM opportunities
            WHERE opportunities.id = ANY(outbox.opportunity_ids)
              AND opportunities.kind = ANY(:kinds)
        )"""
        parameters["kinds"] = selected_kinds
    query += " ORDER BY id" + (" DESC" if newest_first else "")
    if limit is not None:
        query += " LIMIT :limit"
        parameters["limit"] = limit
    if offset:
        query += " OFFSET :offset"
        parameters["offset"] = offset
    statement = text(query)
    result = connection.execute(statement, parameters) if parameters else connection.execute(statement)
    return result.mappings().all()


def _spreadsheet_cell(value: Any) -> str:
    value = "" if value is None else str(value)
    # CSV quoting does not stop spreadsheet formula execution. Prefix risky
    # text (including whitespace before formulas); JSON exports remain exact.
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_outbox_csv(rows: Iterable[Mapping[str, Any]]) -> str:
    """Produce the same UTF-8, spreadsheet-safe CSV for every surface."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        values = {field: row[field] for field in CSV_FIELDS}
        for field in ("created_at", "sent_at"):
            if values[field] is not None:
                values[field] = values[field].astimezone(UTC)
        values["opportunity_ids"] = json.dumps(values["opportunity_ids"], separators=(",", ":"))
        writer.writerow({field: _spreadsheet_cell(value) for field, value in values.items()})
    return output.getvalue()
