"""Canonical ingestion service shared by HTTP and future source adapters."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import MetaData, Table, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from respawned.core.contact import lock_contact_keys
from respawned.core.contracts import ActivityIn, OpportunityIn

Database = Connection | Session
DIALECT_INSERTS = {"postgresql": postgres_insert, "sqlite": sqlite_insert}


class IngestConflictError(ValueError):
    """An immutable activity ID was reused for a different fact."""


class UnknownOpportunityError(ValueError):
    """An activity arrived before its referenced opportunity."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    opportunities_upserted: int
    activities_inserted: int


def _rows(
    records: Iterable[Mapping[str, Any]],
    *,
    kind: str,
    model: type[BaseModel],
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        row = model.model_validate(record).model_dump()
        record_id = row["id"]
        previous = unique.get(record_id)
        if previous is not None and previous != row:
            raise IngestConflictError(
                f"conflicting {kind} payloads for id {record_id!r}"
            )
        unique[record_id] = row
    # New IDs also acquire uniqueness locks during INSERT. Use the same order
    # across batches even when existing-row/contact locks cannot serialize them.
    return [unique[record_id] for record_id in sorted(unique)]


def _bind(database: Database) -> Connection:
    return database.connection() if isinstance(database, Session) else database


def _tables(database: Database) -> tuple[Table, Table]:
    metadata = MetaData()
    bind = _bind(database)
    return (
        Table("opportunities", metadata, autoload_with=bind),
        Table("activities", metadata, autoload_with=bind),
    )


def _dialect_name(database: Database) -> str:
    return _bind(database).dialect.name


def _upsert_opportunities(
    database: Database,
    table: Table,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    mutable = [column.name for column in table.columns if column.name != "id"]
    dialect = _dialect_name(database)
    dialect_insert = DIALECT_INSERTS.get(dialect)
    if dialect_insert:
        statement = dialect_insert(table).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[table.c.id],
            set_={name: statement.excluded[name] for name in mutable},
        )
        database.execute(statement)
        return
    for row in rows:
        result = database.execute(
            update(table)
            .where(table.c.id == row["id"])
            .values(**{name: row.get(name) for name in mutable})
        )
        if not result.rowcount:
            database.execute(insert(table).values(**row))


def _comparable(value: Any) -> Any:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        return Decimal(str(value))
    return value


def _same_activity(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    return all(
        _comparable(existing[name]) == _comparable(value)
        for name, value in incoming.items()
    )


def _existing_activities(
    database: Database,
    table: Table,
    ids: Iterable[str],
) -> dict[str, Mapping[str, Any]]:
    return {
        row["id"]: row
        for row in database.execute(select(table).where(table.c.id.in_(ids))).mappings()
    }


def _affected_contact_keys(
    database: Database,
    opportunity_table: Table,
    opportunity_rows: list[dict[str, Any]],
    activity_rows: list[dict[str, Any]],
) -> tuple[str | None, ...]:
    incoming_ids = {row["id"] for row in opportunity_rows}
    referenced = {row["opportunity_id"] for row in activity_rows}
    affected_ids = incoming_ids | referenced
    existing: dict[str, str | None] = {}
    if affected_ids:
        statement = (
            select(opportunity_table.c.id, opportunity_table.c.contact_key)
            .where(opportunity_table.c.id.in_(affected_ids))
            .order_by(opportunity_table.c.id)
        )
        if _dialect_name(database) == "postgresql":
            statement = statement.with_for_update()
        existing = dict(database.execute(statement).all())

    missing = sorted(referenced - incoming_ids - existing.keys())
    if missing:
        raise UnknownOpportunityError(
            f"activities reference opportunities not yet ingested: {missing!r}"
        )
    return tuple(existing.values()) + tuple(
        row["contact_key"] for row in opportunity_rows
    )


def _new_activities(
    database: Database,
    table: Table,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rows:
        return []
    existing = _existing_activities(database, table, (row["id"] for row in rows))
    missing: list[dict[str, Any]] = []
    for row in rows:
        current = existing.get(row["id"])
        if current is None:
            missing.append(row)
        elif not _same_activity(current, row):
            raise IngestConflictError(
                f"activity id {row['id']!r} already exists with a different payload"
            )
    return missing


def _insert_activities(
    database: Database,
    table: Table,
    rows: list[dict[str, Any]],
) -> int:
    if not rows:
        return 0

    dialect = _dialect_name(database)
    dialect_insert = DIALECT_INSERTS.get(dialect)
    if dialect_insert:
        statement = (
            dialect_insert(table)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[table.c.id])
            .returning(table.c.id)
        )
    else:
        database.execute(insert(table).values(rows))
        return len(rows)

    inserted_ids = set(database.execute(statement).scalars())
    raced = [row for row in rows if row["id"] not in inserted_ids]
    if raced:
        winners = _existing_activities(database, table, (row["id"] for row in raced))
        for row in raced:
            current = winners.get(row["id"])
            if current is None or not _same_activity(current, row):
                raise IngestConflictError(
                    f"activity id {row['id']!r} concurrently received a different payload"
                )
    return len(inserted_ids)


def ingest_records(
    database: Database,
    *,
    opportunities: Iterable[Mapping[str, Any]] = (),
    activities: Iterable[Mapping[str, Any]] = (),
) -> IngestResult:
    """Validate source records, upsert snapshots, and append immutable activities.

    The caller owns the transaction and must roll it back if ingestion fails.
    Record validation completes before any database access or writes.
    """
    opportunity_rows = _rows(
        opportunities,
        kind="opportunity",
        model=OpportunityIn,
    )
    activity_rows = _rows(
        activities,
        kind="activity",
        model=ActivityIn,
    )
    opportunity_table, activity_table = _tables(database)
    lock_contact_keys(
        _bind(database),
        _affected_contact_keys(
            database,
            opportunity_table,
            opportunity_rows,
            activity_rows,
        ),
    )
    new_activities = _new_activities(database, activity_table, activity_rows)
    _upsert_opportunities(database, opportunity_table, opportunity_rows)
    inserted = _insert_activities(database, activity_table, new_activities)
    return IngestResult(len(opportunity_rows), inserted)
