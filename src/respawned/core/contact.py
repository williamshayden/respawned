"""Contact identity, destination normalization, and workflow locking."""

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.core.domain import ContactPoint

CONTACT_LOCK_QUERY = text(
    "SELECT pg_advisory_xact_lock(hashtextextended(:contact_key, 0))"
)
CONTACT_OPPORTUNITY_LOCK_QUERY = text(
    """
    SELECT id
    FROM opportunities
    WHERE contact_key = :contact_key
    ORDER BY id
    FOR UPDATE
    """
)


def normalize_contact_key(value: str | None) -> str | None:
    """Normalize an opaque source identity without deriving it from an address."""

    key = (value or "").strip()
    return key or None


def lock_contact_keys(
    connection: Connection, contact_keys: Iterable[str | None]
) -> tuple[str, ...]:
    """Lock normalized contact identities in deterministic order on Postgres."""

    keys = tuple(
        sorted(
            {
                key
                for value in contact_keys
                if (key := normalize_contact_key(value)) is not None
            }
        )
    )
    if connection.dialect.name == "postgresql":
        for key in keys:
            connection.execute(CONTACT_LOCK_QUERY, {"contact_key": key})
    return keys


def lock_contact_opportunities(
    connection: Connection, contact_key: str
) -> tuple[str, ...]:
    """Lock one contact's opportunity rows before taking its advisory lock."""

    if connection.dialect.name != "postgresql":
        return ()
    return tuple(
        connection.execute(
            CONTACT_OPPORTUNITY_LOCK_QUERY,
            {"contact_key": contact_key},
        ).scalars()
    )


def normalize_contact_address(point: ContactPoint) -> str:
    """Return a stable route address for idempotency keys."""

    if point.channel == "email":
        return point.address.casefold()
    digits = "".join(character for character in point.address if character.isdigit())
    return digits or point.address
