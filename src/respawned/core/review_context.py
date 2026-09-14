"""Deterministic source context for explicit review and automatic draft reuse."""

from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import TYPE_CHECKING

from respawned.core.contact import normalize_contact_key
from respawned.core.domain import OpportunityState
from respawned.core.time import aware_utc

if TYPE_CHECKING:
    from respawned.core.review import PersistedDraft


def _canonical(value):
    if isinstance(value, datetime):
        return aware_utc(value, "review source timestamp").isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def review_source_fingerprint(
    states: Sequence[OpportunityState], contact_key: str,
) -> str:
    """Bind the current contact group's canonical facts and visible evidence.

    The caller supplies the same as-of projection used to display or prepare the
    draft. Time and ranking scores are not inputs; unchanged reimports and later
    reads of the same source facts retain the fingerprint.
    """
    key = normalize_contact_key(contact_key)
    source = []
    for state in sorted(states, key=lambda item: item.opportunity_id):
        if normalize_contact_key(state.contact_key) != key:
            continue
        values = asdict(state)
        values["activities"] = sorted(values["activities"], key=lambda item: (
            aware_utc(item["occurred_at"], "activity.occurred_at"),
            item["activity_id"],
        ))
        values["view_timestamps"] = sorted(values["view_timestamps"])
        source.append(_canonical(values))
    encoded = json.dumps(
        {"version": 1, "contact_key": key, "states": source},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def bind_review_context(
    draft: "PersistedDraft", states: Sequence[OpportunityState],
) -> "PersistedDraft":
    """Attach the source fingerprint represented by this review token.

    Completed reviews retain their accepted snapshot so repeating an approval
    or rejection remains idempotent after later source activity. Legacy reviews
    retain unknown provenance; reading them does not invent a past snapshot.
    """
    fingerprint = (
        review_source_fingerprint(states, draft.contact_key)
        if draft.status == "pending"
        else draft.reviewed_source_fingerprint
    )
    return replace(draft, current_source_fingerprint=fingerprint)
