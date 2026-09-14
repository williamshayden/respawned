"""Read projections for the shared UI; reading never generates or approves copy."""

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Connection

from respawned.core.candidates import Candidate
from respawned.core.contact import normalize_contact_key
from respawned.core.domain import OpportunityState, resolve_contact
from respawned.core.helpers.validate import DraftValidationError, validate_draft
from respawned.core.opportunity import is_contactable_opportunity
from respawned.core.policy import Policy
from respawned.core.review import PersistedDraft
from respawned.core.review_context import bind_review_context
from respawned.core.sync import CandidateSnapshot, read_candidate_snapshot
from respawned.core.time import aware_utc, local_calendar_days_since, within_cooldown


DRAFT_COLUMNS = """
    id, candidate_id, contact_key, contact_address, contact_name, channel,
    primary_opportunity_id, opportunity_ids, body, status,
    created_at, updated_at, reviewed_at,
    generation_source_fingerprint, reviewed_source_fingerprint
"""


def load_ui_draft(connection: Connection, draft_id: UUID) -> PersistedDraft | None:
    row = connection.execute(
        text(f"SELECT {DRAFT_COLUMNS} FROM drafts WHERE id = :id"), {"id": draft_id}
    ).mappings().one_or_none()
    return _persisted_draft(row) if row is not None else None


def _persisted_draft(row: Any) -> PersistedDraft:
    values = dict(row)
    values["opportunity_ids"] = tuple(values["opportunity_ids"])
    return PersistedDraft(**values)


def draft_view(
    draft: PersistedDraft, *, outbox_id: int | None = None,
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": draft.id, "body": draft.body, "status": draft.status,
        "review_token": draft.review_token,
        "validation_errors": validation_errors or [], "outbox_id": outbox_id,
        "source_context_status": (
            "unknown" if draft.generation_source_fingerprint is None
            else "current" if draft.generation_source_fingerprint == draft.current_source_fingerprint
            else "changed"
        ),
    }


def record_references(connection: Connection, record_ids: Iterable[str]) -> dict[str, dict[str, str]]:
    """Resolve source identity separately from the paginated review queue."""
    ids = sorted(set(record_ids))
    if not ids:
        return {}
    rows = connection.execute(text("""
        SELECT id, kind, COALESCE(title, context->>'role', context->>'company', id) AS title
        FROM opportunities WHERE id = ANY(:ids)
    """), {"ids": ids}).mappings()
    return {row["id"]: dict(row) for row in rows}


def _label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").capitalize()


def _reason(code: str, label: str, detail: str) -> dict[str, str]:
    return {"code": code, "label": label, "detail": detail}


def _fields(state: OpportunityState) -> list[dict[str, str]]:
    context = state.context
    values = (
        ("Company", context.company), ("Role", context.role),
        ("Owner", state.owner_name),
        ("Value", str(state.value)
         if state.value is not None and state.kind != "job_application" else None),
    )
    return [{"label": label, "value": value} for label, value in values if value]


def _date(value: datetime, timezone: ZoneInfo) -> str:
    local = value.astimezone(timezone)
    return f"{local:%b} {local.day}, {local.year}"


def _candidate_reason(
    state: OpportunityState, candidate: Candidate, policy: Policy,
    now: datetime, outbound: datetime | None,
) -> dict[str, str]:
    """Explain the configured signal using the same ingested dates it evaluated."""
    timezone = ZoneInfo(policy.business_context.timezone_name)
    configured = policy.reasons[candidate.reason]
    evaluator = configured.evaluator
    label = _label(candidate.reason)
    detail = f"The configured {label.lower()} signal matched this record."
    if evaluator == "promised_update_overdue" and state.context.expected_reply_at is not None:
        label = "Expected update overdue"
        detail = (
            f"An update was expected on {_date(state.context.expected_reply_at, timezone)}. "
            "No human reply or outbound follow-up is recorded after that date."
        )
    elif evaluator == "application_no_update":
        since = max(value for value in (state.created_at, state.last_replied_at, outbound)
                    if value is not None and value <= now)
        days = local_calendar_days_since(since, now, timezone)
        label = "Application update due"
        detail = (
            f"No human update or outbound follow-up since {_date(since, timezone)} "
            f"({days} days). Automated receipts do not reset this wait."
        )
    elif evaluator == "awaiting_reply" and outbound is not None:
        label = "Waiting for a reply"
        detail = f"The outbound message on {_date(outbound, timezone)} has no later human reply."
    elif evaluator == "reply_after_outbound" and state.last_replied_at is not None:
        label = "Reply needs an answer"
        detail = f"A reply on {_date(state.last_replied_at, timezone)} has no later outbound response."
    elif evaluator == "view_after_outbound" and state.last_viewed_at is not None:
        label = "Viewed without a reply"
        detail = f"Content was viewed on {_date(state.last_viewed_at, timezone)}; no later human reply is recorded."
    elif evaluator == "distinct_view_days":
        views = [value for value in state.view_timestamps
                 if value <= now and (outbound is None or value > outbound)]
        if views:
            days = len({value.astimezone(timezone).date() for value in views})
            label = "Repeated interest"
            detail = f"Content was viewed on {days} separate days, most recently {_date(max(views), timezone)}."
    elif evaluator == "high_value_quiet":
        contacts = [value for value in (state.created_at, state.last_replied_at, outbound)
                    if value is not None and value <= now]
        if contacts:
            label = "High-value record quiet"
            detail = f"This record meets the policy's value threshold; no new exchange is recorded since {_date(max(contacts), timezone)}."
    elif evaluator == "opportunity_age" and state.created_at is not None:
        days = local_calendar_days_since(state.created_at, now, timezone)
        label = "Open record needs a check-in"
        detail = f"Open since {_date(state.created_at, timezone)} ({days} days), beyond the configured check-in threshold."
    return _reason(candidate.reason, label, detail)


def list_ui_records(
    connection: Connection, *, now: datetime, policy: Policy,
    limit: int = 50, offset: int = 0, record_id: str | None = None,
    kinds: Iterable[str] | None = None,
    search: str = "", channel: str | None = None,
    view: str = "all", sort: str = "priority",
    snapshot: CandidateSnapshot | None = None,
) -> dict[str, Any]:
    """Retain every tracked record, including records with no delivery route.

    Eligibility is recomputed from current ingested state. Reading does not
    persist candidates; generating a draft prepares only its selected candidate.
    Pagination applies after deterministic priority sorting, so page boundaries
    may change when new evidence arrives and callers should reconcile by ID.
    Saved workspace kinds filter presentation only: eligibility and cooldowns
    always use the complete shared engine state, before filtering or pagination.
    """
    now = aware_utc(now, "list_ui_records.now")
    snapshot = snapshot or read_candidate_snapshot(connection, now=now, policy=policy)
    if snapshot.as_of != now:
        raise ValueError("The record and candidate projections must use the same as-of time")
    states, eligible = snapshot.states, snapshot.candidates
    states_by_contact: dict[str | None, list[OpportunityState]] = {}
    for state in states:
        states_by_contact.setdefault(normalize_contact_key(state.contact_key), []).append(state)
    current = {candidate.primary_opportunity_id: candidate for candidate in eligible}
    drafts = {
        row["primary_opportunity_id"]: _persisted_draft(row)
        for row in connection.execute(text(f"""
            SELECT DISTINCT ON (primary_opportunity_id) {DRAFT_COLUMNS}
            FROM drafts ORDER BY primary_opportunity_id, created_at DESC, id DESC
        """)).mappings()
    }
    outbox = {
        row["draft_id"]: row["id"]
        for row in connection.execute(text("SELECT id, draft_id FROM outbox")).mappings()
    }
    reservations = set(connection.execute(text("""
        SELECT DISTINCT contact_key FROM outbox
        WHERE created_at > :now - make_interval(
            secs => CAST(:seconds AS double precision)
        )
    """), {"now": now, "seconds": policy.cooldown_hours * 3600}).scalars())
    latest_outbounds: dict[str, datetime] = {}
    for state in states:
        if state.contact_key and state.last_outbound_at is not None:
            latest_outbounds[state.contact_key] = max(
                state.last_outbound_at,
                latest_outbounds.get(state.contact_key, state.last_outbound_at),
            )

    timezone = ZoneInfo(policy.business_context.timezone_name)
    selected_kinds = set(kinds or ())
    items: list[dict[str, Any]] = []
    displayed_drafts: dict[str, tuple[PersistedDraft, list[str]]] = {}
    for state in states:
        if record_id is not None and state.opportunity_id != record_id:
            continue
        if selected_kinds and state.kind not in selected_kinds:
            continue
        route = resolve_contact(state)
        candidate = current.get(state.opportunity_id)
        draft = drafts.get(state.opportunity_id)
        # A new signal needs a new draft; old review history belongs to outbox.
        if candidate is not None and draft is not None and draft.candidate_id != candidate.id:
            draft = None
        outbound = latest_outbounds.get(state.contact_key or "")
        human_replies = [
            activity for activity in state.activities
            if (activity.activity_type == "contact_replied"
                or activity.classification == "human")
            and activity.direction == "inbound"
            and activity.classification != "automated"
            and (outbound is None or activity.occurred_at >= outbound)
        ]
        action = "waiting"
        reason = _reason("waiting", "Waiting for a signal", "No follow-up is due under the current policy.")
        if state.status != "open":
            action = "closed"
            reason = _reason("closed", "Record closed", "This record is no longer open for follow-up.")
        elif route is None or not state.contact_key:
            action = "blocked"
            reason = _reason("missing_contact", "Contact needed", "Tracking is active. Add a contact and a supported delivery route before drafting.")
        elif not is_contactable_opportunity(state, now, timezone, policy.dead_after_days):
            action = "blocked"
            reason = _reason("outside_policy_window", "Outside follow-up window", "The record age is outside the current policy window.")
        elif draft is not None and draft.status in {"approved", "rejected"}:
            action = draft.status
            reason = _reason(
                action, "Approved to outbox" if action == "approved" else "Draft rejected",
                "Approval reserves an unsent outbox item; it does not send a message."
                if action == "approved" else "This draft was rejected. A new signal is needed for another candidate.",
            )
        elif human_replies:
            action = "reply"
            latest = max(activity.occurred_at for activity in human_replies)
            reason = _reason("human_reply", "Human reply received", f"A human reply on {_date(latest, timezone)} has no later outbound response for this contact.")
        elif candidate is not None:
            action = "follow_up"
            reason = _candidate_reason(state, candidate, policy, now, outbound)
        elif state.contact_key in reservations:
            reason = _reason("outbox_cooldown", "Outbox reservation active", "Another approved draft has reserved this contact's cooldown window.")
        elif outbound is not None and within_cooldown(outbound, now, policy.cooldown_hours):
            reason = _reason("cooldown", "Contact cooldown active", "A recent outbound message is inside the contact-wide cooldown window.")

        errors: list[str] = []
        if draft is not None and draft.status == "pending":
            if candidate is None or candidate.id != draft.candidate_id:
                errors.append("Eligibility changed. Sync and review the current record before approval.")
            try:
                validate_draft(
                    draft.body, max_characters=policy.drafting.max_characters,
                    opportunity_status=state.status or "", owner_name=state.owner_name,
                    require_owner_name=policy.drafting.require_owner_name,
                )
            except DraftValidationError as exc:
                errors.append(str(exc))
            if errors and action not in {"closed", "blocked"}:
                action = "blocked"
                reason = _reason("draft_needs_review", "Draft needs review", errors[0])
        if draft is not None:
            displayed_drafts[state.opportunity_id] = (draft, errors)
        items.append({
            "id": state.opportunity_id, "kind": state.kind,
            "title": state.title or state.context.role or state.context.company or state.opportunity_id,
            "status": state.status or "unknown", "stage": state.context.stage,
            "source_url": state.context.source_url,
            "contact": ({"key": state.contact_key, "name": state.contact_name,
                         "address": route.address, "channel": route.channel}
                        if route is not None and state.contact_key else None),
            "fields": _fields(state), "reason": reason, "next_action": action,
            "score": float(candidate.score) if candidate is not None else None,
            "last_contact_at": max(
                (value for value in (outbound, state.last_replied_at) if value is not None),
                default=None,
            ),
            "candidate_id": candidate.id if candidate is not None else None,
            "referenced_record_ids": list(candidate.other_opportunity_ids) if candidate is not None
                else [value for value in draft.opportunity_ids if value != state.opportunity_id] if draft else [],
            "activities": [{
                "id": activity.activity_id, "type": activity.activity_type,
                "label": _label(activity.activity_type), "occurred_at": activity.occurred_at,
                "classification": activity.classification,
                "source_url": activity.source_url, "summary": activity.summary,
            } for activity in sorted(state.activities, key=lambda item: (item.occurred_at, item.activity_id), reverse=True)[:100]],
            "source_freshness": "unknown",
            "draft": None,
        })
    priority = {"reply": 0, "follow_up": 1, "blocked": 2, "waiting": 3, "approved": 4, "rejected": 5, "closed": 6}
    items.sort(key=lambda item: (priority[item["next_action"]], -(item["score"] or 0), item["id"]))
    def actionable(item):
        return item["next_action"] in {"reply", "follow_up"} and item["contact"] is not None and item["candidate_id"] is not None

    counts = {"tracked": len(items), "ready": sum(bool(actionable(item)) for item in items)}
    needle = search.strip().casefold()
    items = [item for item in items
             if (view != "ready" or actionable(item))
             and (channel is None or item["contact"] is not None and item["contact"]["channel"] == channel)
             and (not needle or needle in " ".join([
                 item["title"], item["kind"], item["id"],
                 item["contact"]["name"] or "" if item["contact"] else "",
                 item["contact"]["address"] if item["contact"] else "",
                 *[field["value"] for field in item["fields"]], item["reason"]["label"],
             ]).casefold())]
    if sort == "recent":
        items.sort(key=lambda item: (
            -(item["last_contact_at"].timestamp() if item["last_contact_at"] is not None else float("-inf")), item["id"],
        ))
    selected = items[offset:offset + limit]
    for item in selected:
        if saved := displayed_drafts.get(item["id"]):
            draft, errors = saved
            draft = bind_review_context(draft, states_by_contact.get(normalize_contact_key(draft.contact_key), ()))
            item["draft"] = draft_view(draft, outbox_id=outbox.get(draft.id), validation_errors=errors)
    return {"items": selected, "total": len(items), "counts": counts,
            "has_more": offset + limit < len(items), "as_of": now}
