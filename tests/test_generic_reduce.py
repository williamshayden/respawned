from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from respawned.core.domain import Activity, OpportunityState
from respawned.core.reduce import reduce_opportunities
from respawned.core.policy import load_policy
from respawned.core.sync import compute_candidates
from respawned.cli.common import DEFAULT_POLICY_PATH


NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _insert_opportunity(connection, opportunity_id: str, **overrides) -> None:
    values = {
        "id": opportunity_id,
        "contact_key": f"contact:{opportunity_id}",
        "contact_name": "Avery Customer",
        "contact_phone": None,
        "contact_email": "avery@example.com",
        "owner_name": "Morgan Owner",
        "value": Decimal("1250.00"),
        "status": "open",
        "created_at": datetime(2026, 8, 2, tzinfo=UTC),
        "last_contact_at": None,
        "preferred_channel": "email",
    }
    values.update(overrides)
    connection.execute(
        text(
            """
            INSERT INTO opportunities (
                id, contact_key, contact_name, contact_phone, contact_email,
                owner_name, value, status, created_at, last_contact_at,
                preferred_channel
            ) VALUES (
                :id, :contact_key, :contact_name, :contact_phone, :contact_email,
                :owner_name, :value, :status, :created_at, :last_contact_at,
                :preferred_channel
            )
            """
        ),
        values,
    )


def _insert_activities(
    connection,
    opportunity_id: str,
    activities: list[dict],
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO activities (
                id, type, opportunity_id, occurred_at, channel, direction
            ) VALUES (
                :id, :type, :opportunity_id, :occurred_at,
                :channel, :direction
            )
            """
        ),
        [
            {
                "channel": None,
                "direction": None,
                **activity,
                "opportunity_id": opportunity_id,
            }
            for activity in activities
        ],
    )


def _state(connection, opportunity_id: str, *, now: datetime = NOW):
    return next(
        state
        for state in reduce_opportunities(connection, now)
        if state.opportunity_id == opportunity_id
    )


def test_reducer_projects_only_activities_visible_as_of_now(postgres_connection):
    opportunity_id = "generic-as-of"
    _insert_opportunity(postgres_connection, opportunity_id)
    _insert_activities(
        postgres_connection,
        opportunity_id,
        [
            {
                "id": "as-of-1",
                "type": "opportunity_created",
                "occurred_at": datetime(2026, 8, 1, tzinfo=UTC),
            },
            {
                "id": "as-of-2",
                "type": "content_viewed",
                "occurred_at": datetime(2026, 8, 10, tzinfo=UTC),
                "channel": "email",
            },
            {
                "id": "as-of-3",
                "type": "opportunity_won",
                "occurred_at": datetime(2026, 8, 21, tzinfo=UTC),
            },
        ],
    )

    current = _state(postgres_connection, opportunity_id)
    future = _state(
        postgres_connection,
        opportunity_id,
        now=datetime(2026, 8, 22, tzinfo=UTC),
    )

    assert current.status == "open"
    assert current.created_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert current.last_viewed_at == datetime(2026, 8, 10, tzinfo=UTC)
    assert [activity.activity_id for activity in current.activities] == [
        "as-of-1",
        "as-of-2",
    ]
    assert future.status == "won"
    assert [activity.activity_id for activity in future.activities] == [
        "as-of-1",
        "as-of-2",
        "as-of-3",
    ]


def test_conflicting_duplicate_activity_id_collapses_deterministically(
    postgres_connection,
):
    opportunity_id = "generic-duplicate"
    _insert_opportunity(postgres_connection, opportunity_id)
    postgres_connection.execute(
        text("ALTER TABLE activities DROP CONSTRAINT activities_pkey")
    )
    conflicting = [
        {
            "id": "duplicate-id",
            "type": "content_viewed",
            "occurred_at": datetime(2026, 8, 10, tzinfo=UTC),
            "channel": "sms",
        },
        {
            "id": "duplicate-id",
            "type": "contact_replied",
            "occurred_at": datetime(2026, 8, 10, tzinfo=UTC),
            "channel": "email",
            "direction": "inbound",
        },
    ]

    _insert_activities(postgres_connection, opportunity_id, conflicting)
    forward = _state(postgres_connection, opportunity_id)
    postgres_connection.execute(
        text("DELETE FROM activities WHERE opportunity_id = :opportunity_id"),
        {"opportunity_id": opportunity_id},
    )
    _insert_activities(
        postgres_connection,
        opportunity_id,
        list(reversed(conflicting)),
    )
    reverse = _state(postgres_connection, opportunity_id)

    assert forward == reverse
    assert reverse.last_replied_at == datetime(2026, 8, 10, tzinfo=UTC)
    assert reverse.last_viewed_at is None
    assert reverse.activities == (
        Activity(
            activity_id="duplicate-id",
            activity_type="contact_replied",
            occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
            channel="email",
            direction="inbound",
        ),
    )


def test_latest_terminal_activity_updates_open_status(postgres_connection):
    opportunity_id = "generic-status"
    _insert_opportunity(postgres_connection, opportunity_id)
    _insert_activities(
        postgres_connection,
        opportunity_id,
        [
            {
                "id": "status-1",
                "type": "opportunity_won",
                "occurred_at": datetime(2026, 8, 10, tzinfo=UTC),
            },
            {
                "id": "status-2",
                "type": "opportunity_lost",
                "occurred_at": datetime(2026, 8, 11, tzinfo=UTC),
            },
        ],
    )

    assert _state(postgres_connection, opportunity_id).status == "lost"


def test_last_outbound_is_maximum_seed_or_visible_message(postgres_connection):
    opportunity_id = "generic-last-outbound"
    _insert_opportunity(
        postgres_connection,
        opportunity_id,
        last_contact_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    _insert_activities(
        postgres_connection,
        opportunity_id,
        [
            {
                "id": "outbound-1",
                "type": "message_sent",
                "occurred_at": datetime(2026, 8, 9, tzinfo=UTC),
                "channel": "sms",
                "direction": "outbound",
            },
            {
                "id": "outbound-2",
                "type": "message_sent",
                "occurred_at": datetime(2026, 8, 12, tzinfo=UTC),
                "channel": "email",
                "direction": "outbound",
            },
            {
                "id": "outbound-future",
                "type": "message_sent",
                "occurred_at": datetime(2026, 8, 21, tzinfo=UTC),
                "channel": "sms",
                "direction": "outbound",
            },
        ],
    )

    before_stream_contact = _state(
        postgres_connection,
        opportunity_id,
        now=datetime(2026, 8, 11, tzinfo=UTC),
    )
    current = _state(postgres_connection, opportunity_id)

    assert before_stream_contact.last_outbound_at == datetime(
        2026, 8, 10, tzinfo=UTC
    )
    assert current.last_outbound_at == datetime(2026, 8, 12, tzinfo=UTC)
    assert current.preferred_channel == "email"
    assert current.contact_email == "avery@example.com"


def test_future_snapshot_cannot_hide_a_recent_outbound(postgres_connection):
    opportunity_id = "future-snapshot-recent-outbound"
    _insert_opportunity(
        postgres_connection, opportunity_id,
        last_contact_at=NOW + timedelta(days=1),
    )
    recent = NOW - timedelta(hours=1)
    _insert_activities(postgres_connection, opportunity_id, [{
        "id": "recent-visible-outbound", "type": "message_sent",
        "occurred_at": recent, "direction": "outbound", "channel": "email",
    }])
    state = _state(postgres_connection, opportunity_id)
    assert state.last_outbound_at == recent
    assert compute_candidates([state], load_policy(DEFAULT_POLICY_PATH), NOW) == []


def test_reducer_returns_source_neutral_opportunity_state(postgres_connection):
    opportunity_id = "generic-state"
    _insert_opportunity(
        postgres_connection,
        opportunity_id,
        contact_name=None,
    )

    state = _state(postgres_connection, opportunity_id)

    assert state == OpportunityState(
        opportunity_id=opportunity_id,
        status="open",
        value=Decimal("1250.00"),
        contact_key=f"contact:{opportunity_id}",
        contact_email="avery@example.com",
        owner_name="Morgan Owner",
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
        preferred_channel="email",
    )


def test_reducer_can_scope_state_to_one_stable_contact(postgres_connection):
    _insert_opportunity(
        postgres_connection,
        "scope-one",
        contact_key="crm:one",
    )
    _insert_opportunity(
        postgres_connection,
        "scope-two",
        contact_key="crm:two",
    )

    states = reduce_opportunities(
        postgres_connection,
        NOW,
        contact_key="crm:two",
    )

    assert [state.opportunity_id for state in states] == ["scope-two"]
