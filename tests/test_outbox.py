import csv
from io import StringIO
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from respawned.core.outbox import list_outbox_rows, render_outbox_csv
from respawned.core.review import enqueue_outbox

NOW = datetime(2026, 8, 20, 14, tzinfo=UTC)


def _insert_draft(
    connection,
    label: str,
    *,
    contact_key: str,
    contact_address: str,
    channel: str,
):
    opportunity_id = f"opp-{label}"
    run_id = uuid4()
    candidate_id = uuid4()
    draft_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO opportunities (
                id, contact_key, contact_name, contact_phone, contact_email,
                status, created_at, preferred_channel
            ) VALUES (
                :id, :contact_key, 'Jamie', :phone, :email,
                'open', :now, :channel
            )
            """
        ),
        {
            "id": opportunity_id,
            "contact_key": contact_key,
            "phone": contact_address if channel == "sms" else None,
            "email": contact_address if channel == "email" else None,
            "channel": channel,
            "now": NOW,
        },
    )
    connection.execute(
        text(
            "INSERT INTO sync_runs (id, run_at, candidate_count) VALUES (:id, :now, 1)"
        ),
        {"id": run_id, "now": NOW},
    )
    connection.execute(
        text(
            """
            INSERT INTO candidates (
                id, sync_run_id, run_at, primary_opportunity_id, contact_key,
                contact_address, contact_name, channel, reason, score
            ) VALUES (
                :id, :run_id, :now, :opportunity_id, :contact_key,
                :contact_address, 'Jamie', :channel, 'aging', 1
            )
            """
        ),
        {
            "id": candidate_id,
            "run_id": run_id,
            "now": NOW,
            "opportunity_id": opportunity_id,
            "contact_key": contact_key,
            "contact_address": contact_address,
            "channel": channel,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO drafts (
                id, candidate_id, contact_key, contact_address, contact_name,
                channel, primary_opportunity_id, opportunity_ids, body
            ) VALUES (
                :id, :candidate_id, :contact_key, :contact_address, 'Jamie',
                :channel, :opportunity_id, :opportunity_ids, 'Original draft'
            )
            """
        ),
        {
            "id": draft_id,
            "candidate_id": candidate_id,
            "contact_key": contact_key,
            "contact_address": contact_address,
            "channel": channel,
            "opportunity_id": opportunity_id,
            "opportunity_ids": [opportunity_id],
        },
    )
    return draft_id, opportunity_id


def test_enqueueing_same_draft_twice_preserves_original_snapshot(
    postgres_connection,
):
    draft_id, opportunity_id = _insert_draft(
        postgres_connection,
        "duplicate",
        contact_key="crm:duplicate",
        contact_address="+15550000001",
        channel="sms",
    )
    first = enqueue_outbox(
        postgres_connection,
        draft_id=draft_id,
        contact_key="crm:duplicate",
        contact_address="+15550000001",
        contact_name="Jamie",
        channel="sms",
        opportunity_ids=[opportunity_id],
        body="Hi Jamie, just checking in.",
        created_at=NOW,
    )
    second = enqueue_outbox(
        postgres_connection,
        draft_id=draft_id,
        contact_key="crm:conflict",
        contact_address="other@example.com",
        contact_name="Other",
        channel="email",
        opportunity_ids=[opportunity_id],
        body="This retry must not replace the original.",
        created_at=NOW,
    )
    row = postgres_connection.execute(
        text(
            """
            SELECT id, contact_key, contact_address, contact_name, channel, body
            FROM outbox WHERE draft_id = :draft_id
            """
        ),
        {"draft_id": draft_id},
    ).one()

    assert first == second == row.id
    assert tuple(row)[1:] == (
        "crm:duplicate",
        "+15550000001",
        "Jamie",
        "sms",
        "Hi Jamie, just checking in.",
    )


def test_enqueueing_different_drafts_creates_distinct_rows(postgres_connection):
    first_draft, first_opportunity = _insert_draft(
        postgres_connection,
        "one",
        contact_key="crm:one",
        contact_address="+15550000003",
        channel="sms",
    )
    second_draft, second_opportunity = _insert_draft(
        postgres_connection,
        "two",
        contact_key="crm:two",
        contact_address="two@example.com",
        channel="email",
    )
    first = enqueue_outbox(
        postgres_connection,
        draft_id=first_draft,
        contact_key="crm:one",
        contact_address="+15550000003",
        contact_name="Jamie",
        channel="sms",
        opportunity_ids=[first_opportunity],
        body="First message",
    )
    second = enqueue_outbox(
        postgres_connection,
        draft_id=second_draft,
        contact_key="crm:two",
        contact_address="two@example.com",
        contact_name="Jamie",
        channel="email",
        opportunity_ids=[second_opportunity],
        body="Second message",
    )

    assert first != second
    assert (
        postgres_connection.execute(text("SELECT COUNT(*) FROM outbox")).scalar_one()
        == 2
    )


@pytest.mark.parametrize("authorization_mode", ("human", "automatic", "legacy_unknown"))
def test_outbox_core_renders_generic_deterministic_csv(
    postgres_connection, authorization_mode
):
    # Export timestamps must not depend on the server/session timezone.
    postgres_connection.exec_driver_sql("SET LOCAL TIME ZONE 'America/New_York'")
    draft_id, opportunity_id = _insert_draft(
        postgres_connection,
        "export",
        contact_key="crm:export",
        contact_address="jamie@example.com",
        channel="email",
    )
    outbox_id = enqueue_outbox(
        postgres_connection,
        draft_id=draft_id,
        contact_key="crm:export",
        contact_address="jamie@example.com",
        contact_name="Jamie",
        channel="email",
        opportunity_ids=[opportunity_id],
        body="First, with a comma",
        created_at=NOW,
        authorization_mode=authorization_mode,
    )
    snapshot = list_outbox_rows(postgres_connection)
    assert len(snapshot) == 1
    rows = list(csv.reader(StringIO(render_outbox_csv(snapshot))))

    assert rows == [
        [
            "id",
            "draft_id",
            "contact_key",
            "contact_address",
            "contact_name",
            "channel",
            "opportunity_ids",
            "body",
            "status",
            "authorization_mode",
            "created_at",
            "sent_at",
        ],
        [
            str(outbox_id),
            str(draft_id),
            "crm:export",
            "jamie@example.com",
            "Jamie",
            "email",
            f'["{opportunity_id}"]',
            "First, with a comma",
            "pending",
            authorization_mode,
            "2026-08-20 14:00:00+00:00",
            "",
        ],
    ]
