import csv
from datetime import UTC, datetime

from sqlalchemy import text


def test_enqueueing_same_draft_twice_creates_one_outbox_row(postgres_connection):
    from respawned.cli.outbox import enqueue_outbox

    first_id = enqueue_outbox(
        postgres_connection,
        draft_id="draft-duplicate",
        body="Hi Jamie, just checking in.",
        channel="sms",
        customer_phone="+15550000001",
        created_at=datetime(2026, 8, 20, 14, 0, tzinfo=UTC),
    )
    second_id = enqueue_outbox(
        postgres_connection,
        draft_id="draft-duplicate",
        body="This conflicting retry must not replace the original.",
        channel="email",
        customer_phone="+15550000002",
        created_at=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
    )

    rows = postgres_connection.execute(
        text(
            """
            SELECT id, draft_id, body, channel, status, customer_phone
            FROM outbox
            WHERE draft_id = 'draft-duplicate'
            """
        )
    ).all()

    assert second_id == first_id
    assert rows == [
        (
            first_id,
            "draft-duplicate",
            "Hi Jamie, just checking in.",
            "sms",
            "pending",
            "+15550000001",
        )
    ]


def test_enqueueing_different_drafts_creates_distinct_rows(postgres_connection):
    from respawned.cli.outbox import enqueue_outbox

    first_id = enqueue_outbox(
        postgres_connection,
        draft_id="draft-one",
        body="First message",
        channel="sms",
        customer_phone="+15550000003",
    )
    second_id = enqueue_outbox(
        postgres_connection,
        draft_id="draft-two",
        body="Second message",
        channel="email",
        customer_phone="+15550000004",
    )

    rows = postgres_connection.execute(
        text(
            """
            SELECT draft_id, body, channel, customer_phone
            FROM outbox
            WHERE draft_id IN ('draft-one', 'draft-two')
            ORDER BY draft_id
            """
        )
    ).all()

    assert first_id != second_id
    assert rows == [
        ("draft-one", "First message", "sms", "+15550000003"),
        ("draft-two", "Second message", "email", "+15550000004"),
    ]


def test_export_outbox_writes_deterministic_csv(postgres_connection, tmp_path):
    from respawned.cli.outbox import enqueue_outbox, export_outbox

    postgres_connection.execute(text("TRUNCATE outbox RESTART IDENTITY"))
    enqueue_outbox(
        postgres_connection,
        draft_id="draft-export-one",
        body="First, with a comma",
        channel="sms",
        customer_phone="+15550000005",
        created_at=datetime(2026, 8, 20, 16, 0, tzinfo=UTC),
    )
    enqueue_outbox(
        postgres_connection,
        draft_id="draft-export-two",
        body="Second message",
        channel="email",
        customer_phone="+15550000006",
        created_at=datetime(2026, 8, 20, 17, 0, tzinfo=UTC),
    )
    path = tmp_path / "outbox.csv"

    exported_count = export_outbox(postgres_connection, path)

    with path.open(newline="", encoding="utf-8") as exported:
        rows = list(csv.reader(exported))

    assert exported_count == 2
    assert rows == [
        [
            "id",
            "draft_id",
            "body",
            "channel",
            "status",
            "created_at",
            "sent_at",
            "customer_phone",
        ],
        [
            "1",
            "draft-export-one",
            "First, with a comma",
            "sms",
            "pending",
            "2026-08-20 16:00:00+00:00",
            "",
            "+15550000005",
        ],
        [
            "2",
            "draft-export-two",
            "Second message",
            "email",
            "pending",
            "2026-08-20 17:00:00+00:00",
            "",
            "+15550000006",
        ],
    ]
