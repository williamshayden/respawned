"""Real PostgreSQL source freshness, human review, and automatic reuse."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from respawned.config import DEFAULT_POLICY_PATH
from respawned.core.ingest import ingest_records
from respawned.core.policy import ReviewPolicy, load_policy
from respawned.core.reduce import reduce_opportunities
from respawned.core.review import (
    ReviewBlockedError, _load_draft, approve_draft, authorize_draft_automatically,
    draft_candidate, reject_draft, update_draft_message,
)
from respawned.core.review_context import bind_review_context, review_source_fingerprint
from respawned.core.sync import sync_candidates
from respawned.core.workflow import process_candidates
from respawned.db.helpers.pg_connect import create_tables


NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)
BODY = "Hi Avery, following up on the Backend Engineer role at Alpha."
POLICY = load_policy(DEFAULT_POLICY_PATH)


class NoModel:
    def complete(self, _messages):
        pytest.fail("This workflow must not invoke a model")


@pytest.fixture
def context_engine(postgres_engine):
    schema = "respawned_context_" + uuid4().hex
    engine = create_engine(postgres_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        create_tables(engine)
        yield engine
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


def record(**values):
    return {
        "id": "review-context:one", "kind": "job_application",
        "title": "Backend Engineer at Alpha", "status": "open",
        "created_at": NOW - timedelta(days=8),
        "contact_key": "review-context:recipient", "contact_name": "Avery",
        "contact_email": "avery@example.com", "preferred_channel": "email",
        "context": {"company": "Alpha", "role": "Backend Engineer"},
        **values,
    }


def prepare(engine, source=None):
    source = source or record()
    with engine.begin() as connection:
        ingest_records(connection, opportunities=[source])
        candidate = sync_candidates(connection, now=NOW, policy=POLICY).candidates[0]
        draft = draft_candidate(connection, candidate=candidate, now=NOW, policy=POLICY,
                                adapter=NoModel(), body=BODY)
    return source, candidate, draft


def fresh(connection, identity, now=NOW):
    return bind_review_context(_load_draft(connection, identity),
                               reduce_opportunities(connection, now))


def rows(engine, table):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]


@pytest.mark.parametrize("mode", ["human", "automatic"])
def test_postponed_deadline_blocks_a_previously_eligible_saved_draft(context_engine, mode):
    source, _candidate, draft = prepare(context_engine, record(
        kind="project", created_at=NOW-timedelta(days=3),
        context={"expected_reply_at": (NOW-timedelta(days=2)).isoformat()},
    ))
    with context_engine.begin() as connection:
        ingest_records(connection, opportunities=[source | {
            "context": {"expected_reply_at": (NOW+timedelta(days=4)).isoformat()},
        }])
        assert not sync_candidates(connection, now=NOW, policy=POLICY, dry_run=True).candidates
        current = fresh(connection, draft.id)
        action = approve_draft if mode == "human" else authorize_draft_automatically
        policy = POLICY if mode == "human" else replace(POLICY, review=ReviewPolicy("automatic"))
        with pytest.raises(ReviewBlockedError, match="no longer eligible"):
            action(connection, draft_id=draft.id, expected_review_token=current.review_token,
                   now=NOW, policy=policy)
    assert rows(context_engine, "outbox") == []
    assert rows(context_engine, "drafts")[0]["status"] == "pending"


@pytest.mark.parametrize("change", [
    {"title": "Designer at Beta"},
    {"contact_name": "Morgan"},
    {"context": {"company": "Beta", "role": "Designer"}},
    {"context": {"company": "Alpha", "role": "Backend Engineer", "summary": "A new next step."}},
])
@pytest.mark.parametrize("action_name", ["approve", "edit", "reject"])
def test_stale_source_context_blocks_human_mutations(context_engine, change, action_name):
    source, candidate, draft = prepare(context_engine)
    with context_engine.begin() as connection:
        ingest_records(connection, opportunities=[source | change])
        assert sync_candidates(connection, now=NOW, policy=POLICY, dry_run=True).candidates[0].id == candidate.id
        action = {"approve": approve_draft, "edit": update_draft_message, "reject": reject_draft}[action_name]
        arguments = {"draft_id": draft.id, "expected_review_token": draft.review_token, "now": NOW}
        if action_name != "reject":
            arguments["policy"] = POLICY
        if action_name == "edit":
            arguments["body"] = "Hi Morgan, is there an update?"
        with pytest.raises(ReviewBlockedError, match="changed since it was shown"):
            action(connection, **arguments)
    saved = rows(context_engine, "drafts")[0]
    assert saved["status"] == "pending" and saved["body"] == BODY and saved["contact_name"] == "Avery"
    assert rows(context_engine, "outbox") == []


def test_fresh_human_review_preserves_exact_copy_and_recipient_then_retries(context_engine):
    source, _candidate, draft = prepare(context_engine)
    with context_engine.begin() as connection:
        ingest_records(connection, opportunities=[source | {"contact_name": "Morgan"}])
        current = fresh(connection, draft.id)
        assert current.review_token != draft.review_token
        outbox_id = approve_draft(connection, draft_id=draft.id,
                                  expected_review_token=current.review_token, now=NOW, policy=POLICY)
    item = rows(context_engine, "outbox")[0]
    assert item["body"] == BODY and item["contact_name"] == "Avery" and item["status"] == "pending"
    with context_engine.begin() as connection:
        ingest_records(connection, opportunities=[source | {"title": "Another current title"}])
        assert fresh(connection, draft.id).review_token == current.review_token
        assert approve_draft(connection, draft_id=draft.id,
                             expected_review_token=current.review_token, now=NOW+timedelta(days=1),
                             policy=POLICY) == outbox_id
    assert len(rows(context_engine, "outbox")) == 1


def test_fresh_human_edit_acknowledges_context_for_later_automatic_processing(context_engine):
    source, _candidate, draft = prepare(context_engine)
    with context_engine.begin() as connection:
        ingest_records(connection, opportunities=[source | {"contact_name": "Morgan"}])
        current = fresh(connection, draft.id)
        updated = update_draft_message(
            connection, draft_id=draft.id, expected_review_token=current.review_token,
            body="Hi Morgan, following up on the Backend Engineer role at Alpha.",
            now=NOW, policy=POLICY,
        )
        assert updated.contact_name == "Morgan"
        assert updated.generation_source_fingerprint == updated.current_source_fingerprint
        outbox_id = authorize_draft_automatically(
            connection, draft_id=draft.id, expected_review_token=updated.review_token,
            now=NOW, policy=replace(POLICY, review=ReviewPolicy("automatic")),
        )
    item = rows(context_engine, "outbox")[0]
    assert item["id"] == outbox_id and item["contact_name"] == "Morgan"
    assert item["authorization_mode"] == "automatic"


def test_automatic_reuse_cannot_refresh_away_changed_preparation_context(context_engine):
    source, _candidate, draft = prepare(context_engine)
    with context_engine.begin() as connection:
        ingest_records(connection, opportunities=[source | {
            "context": {"company": "Beta", "role": "Designer"},
        }])
    result = process_candidates(
        context_engine, policy=replace(POLICY, review=ReviewPolicy("automatic")),
        adapter=NoModel(), clock=lambda: NOW,
    )
    assert result.items[0].status == "blocked"
    assert "preparation source context" in result.items[0].detail
    assert rows(context_engine, "outbox") == []
    saved = rows(context_engine, "drafts")[0]
    assert saved["id"] == draft.id and saved["generation_source_fingerprint"] == draft.generation_source_fingerprint


def test_noop_reimport_and_later_read_preserve_source_review_token(context_engine):
    source, _candidate, draft = prepare(context_engine)
    with context_engine.begin() as connection:
        ingest_records(connection, opportunities=[source | {
            "created_at": source["created_at"].astimezone(timezone(timedelta(hours=-4))),
        }])
        current = fresh(connection, draft.id, NOW+timedelta(minutes=5))
        assert current.review_token == draft.review_token
        assert current.generation_source_fingerprint == current.current_source_fingerprint
        authorize_draft_automatically(
            connection, draft_id=draft.id, expected_review_token=current.review_token,
            now=NOW+timedelta(minutes=5), policy=replace(POLICY, review=ReviewPolicy("automatic")),
        )
    assert len(rows(context_engine, "outbox")) == 1


@pytest.mark.parametrize("mode", ["human", "automatic"])
@pytest.mark.parametrize("change", ["deadline", "company", "new_activity"])
def test_source_changes_during_completion_cannot_save_or_authorize(context_engine, mode, change):
    source = record(kind="project", created_at=NOW-timedelta(days=3),
                    context={"expected_reply_at": (NOW-timedelta(days=2)).isoformat()})
    with context_engine.begin() as connection:
        ingest_records(connection, opportunities=[source])
    clock = SimpleNamespace(now=NOW)
    calls = []

    class InterleavedCompletion:
        def complete(self, _messages):
            calls.append("completion")
            clock.now = NOW + timedelta(seconds=2)
            with context_engine.begin() as connection:
                if change == "new_activity":
                    ingest_records(connection, activities=[{
                        "id": "review-context:new-note", "opportunity_id": source["id"],
                        "type": "source_note", "summary": "New source evidence",
                        "occurred_at": NOW+timedelta(seconds=1),
                    }])
                else:
                    context = ({"expected_reply_at": (NOW+timedelta(days=4)).isoformat()}
                               if change == "deadline"
                               else source["context"] | {"company": "Beta"})
                    ingest_records(connection, opportunities=[source | {"context": context}])
            return "Hi Avery, following up on the update that was due."

    result = process_candidates(
        context_engine, policy=replace(POLICY, review=ReviewPolicy(mode)),
        adapter=InterleavedCompletion(), clock=lambda: clock.now,
    )
    assert result.items[0].status == "blocked"
    assert calls == ["completion"]
    assert rows(context_engine, "drafts") == rows(context_engine, "outbox") == []


def test_approval_reads_fresh_time_after_waiting_for_source_lock(context_engine, monkeypatch):
    import respawned.core.review as review
    source, _candidate, draft = prepare(context_engine)
    waiting = Event()
    real_lock = review.lock_contact_opportunities
    clock = SimpleNamespace(now=NOW)

    def wait_for_source(connection, contact_key):
        waiting.set()
        return real_lock(connection, contact_key)

    monkeypatch.setattr(review, "lock_contact_opportunities", wait_for_source)

    def approve():
        with context_engine.begin() as connection:
            with pytest.raises(ReviewBlockedError, match="changed since it was shown"):
                approve_draft(connection, draft_id=draft.id, expected_review_token=draft.review_token,
                              now=NOW, policy=POLICY, clock=lambda: clock.now)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with context_engine.begin() as writer:
            writer.execute(text("SELECT id FROM opportunities WHERE id=:id FOR UPDATE"), {"id":source["id"]})
            ingest_records(writer, activities=[{
                "id": "review-context:late-note", "opportunity_id": source["id"],
                "type": "source_note", "summary": "A newly visible fact",
                "occurred_at": NOW+timedelta(seconds=1),
            }])
            future = pool.submit(approve)
            assert waiting.wait(timeout=5)
            clock.now = NOW+timedelta(seconds=2)
        future.result(timeout=10)
    assert rows(context_engine, "outbox") == []


def test_concurrent_generation_persists_one_copy_and_preparation_context(context_engine):
    with context_engine.begin() as connection:
        ingest_records(connection, opportunities=[record()])
        candidate = sync_candidates(connection, now=NOW, policy=POLICY).candidates[0]
    simultaneous = Barrier(2)

    class Completion:
        def complete(self, _messages):
            simultaneous.wait(timeout=5)
            return BODY

    def generate():
        with context_engine.begin() as connection:
            return draft_candidate(connection, candidate=candidate, now=NOW, policy=POLICY, adapter=Completion())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(generate) for _ in range(2)]
        first, second = [future.result(timeout=15) for future in futures]
    assert first.id == second.id and first.body == second.body
    assert first.review_token == second.review_token
    assert len(rows(context_engine, "drafts")) == 1 and rows(context_engine, "outbox") == []


def test_migration_keeps_existing_copy_and_unknown_preparation_until_human_review(context_engine):
    _source, _candidate, draft = prepare(context_engine)
    original = rows(context_engine, "drafts")[0]
    with context_engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE drafts DROP COLUMN generation_source_fingerprint")
        connection.exec_driver_sql("ALTER TABLE drafts DROP COLUMN reviewed_source_fingerprint")
    create_tables(context_engine)
    create_tables(context_engine)
    migrated = rows(context_engine, "drafts")[0]
    assert migrated["generation_source_fingerprint"] is migrated["reviewed_source_fingerprint"] is None
    assert {key:value for key,value in original.items() if not key.endswith("source_fingerprint")} == {
        key:value for key,value in migrated.items() if not key.endswith("source_fingerprint")
    }
    with context_engine.begin() as connection:
        current = fresh(connection, draft.id)
        with pytest.raises(ReviewBlockedError, match="changed or unknown"):
            authorize_draft_automatically(
                connection, draft_id=draft.id, expected_review_token=current.review_token,
                now=NOW, policy=replace(POLICY, review=ReviewPolicy("automatic")),
            )
        approve_draft(connection, draft_id=draft.id, expected_review_token=current.review_token,
                      now=NOW, policy=POLICY)
    assert rows(context_engine, "outbox")[0]["authorization_mode"] == "human"
