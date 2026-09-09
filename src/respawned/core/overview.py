"""Unpaginated monitoring counts from the shared engine's current state."""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.core.inbox import reply_inbox_items
from respawned.core.policy import Policy
from respawned.core.reduce import reduce_opportunities
from respawned.core.sync import sync_candidates
from respawned.core.time import aware_utc
from respawned.core.workspaces import list_workspaces


def workspace_overview(connection: Connection, *, now: datetime, policy: Policy) -> dict[str, Any]:
    """Read every saved view without persisting a sync or invoking a model.

    ``records`` includes closed and contactless tracked records. ``ready`` counts
    globally eligible contact-group candidates, including candidates with a
    pending draft, in the view containing their primary record. ``pending_drafts``
    includes all pending draft history, even drafts needing a new eligibility
    check. ``reply_contacts`` counts canonical unanswered reply groups, independent
    of outreach cooldowns. ``pending_outbox`` counts unsent reservations.

    Draft/outbox groups belong to every view containing any referenced record;
    reply groups belong to every view containing reply evidence. Counts in
    overlapping views are deliberately not additive. Source freshness is unknown.
    """
    now = aware_utc(now, "workspace_overview.now")
    states = reduce_opportunities(connection, now)
    kinds_by_id = {state.opportunity_id: state.kind for state in states}
    ready = sync_candidates(connection, now=now, policy=policy, dry_run=True,
                            limit=len(states)).candidates
    replies = reply_inbox_items(states, now=now, policy=policy)
    pending_drafts = connection.execute(text("""
        SELECT opportunity_ids FROM drafts WHERE status = 'pending'
    """)).scalars().all()
    pending_outbox = connection.execute(text("""
        SELECT opportunity_ids FROM outbox WHERE status = 'pending'
    """)).scalars().all()
    workspaces = [{"id": "all", "name": "All work", "description": "Every tracked record in this engine.",
                   "kinds": []}, *list_workspaces(connection)["items"]]
    result = []
    for workspace in workspaces:
        selected = set(workspace["kinds"])

        def matches(record_ids):
            return not selected or any(kinds_by_id.get(record_id) in selected for record_id in record_ids)

        result.append({
            "id": str(workspace["id"]), "name": workspace["name"],
            "description": workspace["description"], "kinds": workspace["kinds"],
            "counts": {
                "records": sum(matches([state.opportunity_id]) for state in states),
                "ready": sum(matches([candidate.primary_opportunity_id]) for candidate in ready),
                "pending_drafts": sum(matches(record_ids) for record_ids in pending_drafts),
                "reply_contacts": sum(matches(item.opportunity_ids) for item in replies),
                "pending_outbox": sum(matches(record_ids) for record_ids in pending_outbox),
            },
        })
    return {"generated_at": now, "source_freshness": "unknown", "workspaces": result}
