"""Authorization modes share safety rules without fabricating human review."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.ingest import ingest_records
from respawned.core.policy import ReviewPolicy, load_policy
from respawned.core.review import (
    ReviewBlockedError,
    approve_draft,
    authorize_draft_automatically,
    draft_candidate,
    enqueue_outbox,
    reject_draft,
    update_draft_message,
)
from respawned.core.sync import sync_candidates
from respawned.db.helpers.pg_connect import DEFAULT_SCHEMA_PATH
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


@pytest.fixture
def policy():
    return replace(load_policy(DEFAULT_POLICY_PATH), review=ReviewPolicy("automatic"))


def _prepare(connection, policy, *, suffix="one", contact_key=None):
    opportunity_id = f"review-toggle-{suffix}"
    ingest_records(
        connection,
        opportunities=[{
            "id": opportunity_id,
            "contact_key": contact_key or f"crm:review-toggle-{suffix}",
            "contact_name": "Jamie",
            "contact_email": "jamie@example.com",
            "created_at": NOW - timedelta(days=2),
            "status": "open",
        }],
        activities=[{
            "id": f"reply-{suffix}",
            "opportunity_id": opportunity_id,
            "type": "contact_replied",
            "occurred_at": NOW - timedelta(hours=1),
            "channel": "email",
            "direction": "inbound",
        }],
    )
    candidates = sync_candidates(connection, now=NOW, policy=policy).candidates
    candidate = next(
        item for item in candidates if item.primary_opportunity_id == opportunity_id
    )
    return draft_candidate(
        connection,
        candidate=candidate,
        now=NOW,
        policy=policy,
        adapter=LiteLLMAdapter(
            proxy_url="http://unused.test",
            master_key="test-key",
            model_alias="scripted",
            completion_fn=lambda **_: {
                "choices": [{"message": {"content": "Hi Jamie, thanks for your reply."}}]
            },
        ),
    )


def _authorize(connection, draft, policy, authorize=authorize_draft_automatically):
    return authorize(
        connection,
        draft_id=draft.id,
        expected_review_token=draft.review_token,
        now=NOW,
        policy=policy,
    )


def _decision(connection, draft):
    return connection.execute(
        text("""
            SELECT outbox.id, outbox.body, outbox.authorization_mode,
                   drafts.status, drafts.reviewed_at
            FROM outbox JOIN drafts ON drafts.id = outbox.draft_id
            WHERE drafts.id = :draft_id
        """),
        {"draft_id": draft.id},
    ).mappings().one()


def test_human_default_blocks_automatic_authorization(postgres_connection, policy):
    draft = _prepare(postgres_connection, policy)
    with pytest.raises(ReviewBlockedError, match="requires review.mode=automatic"):
        _authorize(postgres_connection, draft, load_policy(DEFAULT_POLICY_PATH))
    assert postgres_connection.execute(text("SELECT count(*) FROM outbox")).scalar_one() == 0
    assert postgres_connection.execute(text("SELECT status FROM drafts")).scalar_one() == "pending"


@pytest.mark.parametrize("first_mode", ("human", "automatic"))
def test_authorization_is_idempotent_and_cannot_relabel_previous_decision(
    postgres_connection, policy, first_mode
):
    draft = _prepare(postgres_connection, policy)
    first_function = approve_draft if first_mode == "human" else authorize_draft_automatically
    first_id = _authorize(postgres_connection, draft, policy, first_function)
    for function in (approve_draft, authorize_draft_automatically, first_function):
        assert _authorize(postgres_connection, draft, policy, function) == first_id
    decision = _decision(postgres_connection, draft)
    assert decision["authorization_mode"] == first_mode
    assert decision["body"] == draft.body
    assert decision["status"] == "approved"
    assert decision["reviewed_at"] == (NOW if first_mode == "human" else None)
    assert postgres_connection.execute(text("SELECT count(*) FROM outbox")).scalar_one() == 1


@pytest.mark.parametrize("mode", ("human", "automatic"))
@pytest.mark.parametrize(
    ("change", "error"),
    (
        ("copy", "changed since it was shown"),
        ("route", "contact route changed"),
        ("closed", "no longer open/contactable"),
        ("cooldown", "cooldown"),
        ("invalid_copy", "currency"),
        ("rejected", "already been rejected"),
    ),
)
def test_both_modes_preserve_authorization_guards(
    postgres_connection, policy, mode, change, error
):
    draft = _prepare(postgres_connection, policy)
    if change == "copy":
        update_draft_message(
            postgres_connection,
            draft_id=draft.id,
            expected_review_token=draft.review_token,
            body="Hi Jamie, how can we help?",
            now=NOW,
            policy=policy,
        )
    elif change == "route":
        postgres_connection.execute(
            text("UPDATE opportunities SET contact_email='changed@example.com'")
        )
    elif change == "closed":
        postgres_connection.execute(text("UPDATE opportunities SET status='lost'"))
    elif change == "cooldown":
        postgres_connection.execute(
            text("UPDATE opportunities SET last_contact_at=:now"), {"now": NOW}
        )
    elif change == "invalid_copy":
        # Stored copy may predate a stricter validator or have been imported.
        # Match its token so this specifically exercises authorization validation.
        body = "Hi Jamie, please pay $100."
        postgres_connection.execute(text("UPDATE drafts SET body=:body"), {"body": body})
        draft = replace(draft, body=body)
    else:
        reject_draft(
            postgres_connection,
            draft_id=draft.id,
            expected_review_token=draft.review_token,
            now=NOW,
        )
    function = approve_draft if mode == "human" else authorize_draft_automatically
    with pytest.raises(ReviewBlockedError, match=error):
        _authorize(postgres_connection, draft, policy, function)
    assert postgres_connection.execute(text("SELECT count(*) FROM outbox")).scalar_one() == 0


def test_automatic_authorization_observes_contact_wide_outbox_reservation(
    postgres_connection, policy
):
    first = _prepare(
        postgres_connection, policy, suffix="z-first", contact_key="crm:shared"
    )
    second = _prepare(
        postgres_connection, policy, suffix="a-second", contact_key="crm:shared"
    )
    _authorize(postgres_connection, first, policy)
    with pytest.raises(ReviewBlockedError, match="cooldown"):
        _authorize(postgres_connection, second, policy)
    assert postgres_connection.execute(text("SELECT count(*) FROM outbox")).scalar_one() == 1


def test_additive_schema_upgrade_preserves_legacy_reservation_without_claiming_review(
    postgres_connection, policy
):
    draft = _prepare(postgres_connection, policy)
    reservation = enqueue_outbox(
        postgres_connection,
        draft_id=draft.id,
        contact_key=draft.contact_key,
        contact_address=draft.contact_address,
        contact_name=draft.contact_name,
        channel=draft.channel,
        opportunity_ids=draft.opportunity_ids,
        body=draft.body,
        created_at=NOW,
    )
    # Reproduce the previous schema in this transaction's isolated test schema.
    postgres_connection.exec_driver_sql("ALTER TABLE outbox DROP COLUMN authorization_mode")
    before = dict(postgres_connection.execute(text("SELECT * FROM outbox")).mappings().one())
    schema = DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8")
    postgres_connection.exec_driver_sql(schema)
    postgres_connection.exec_driver_sql(schema)
    after = dict(postgres_connection.execute(text("SELECT * FROM outbox")).mappings().one())
    assert after.pop("authorization_mode") == "legacy_unknown"
    assert after == before
    assert _authorize(postgres_connection, draft, policy) == reservation
    assert _decision(postgres_connection, draft)["authorization_mode"] == "legacy_unknown"
