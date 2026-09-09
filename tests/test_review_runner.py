from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from io import StringIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from rich.console import Console

from respawned.cli.review import PersistedDraft, ReviewSummary, run_review
from respawned.cli.ui import prompt_choice
from respawned.core.context import BusinessContext
from respawned.core.policy import (
    DraftingPolicy,
    Policy,
    RankingPolicy,
    ReasonPolicy,
)
from respawned.llm.adapter import LiteLLMAdapter

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


class _Engine:
    def begin(self):
        return nullcontext(object())


def _console(*, width: int = 120) -> Console:
    return Console(
        file=StringIO(),
        record=True,
        force_terminal=False,
        no_color=True,
        width=width,
    )


def _policy() -> Policy:
    return Policy(
        business_context=BusinessContext("UTC"),
        cooldown_hours=Decimal(72),
        dead_after_days=45,
        ranking=RankingPolicy(
            value_weight=Decimal(0),
            signal_weight=Decimal(0),
        ),
        reasons={
            "replied_no_answer": ReasonPolicy(
                evaluator="reply_after_outbound",
                base_score=Decimal(1),
                tone="warm",
                params={"horizon_days": 7},
            )
        },
        drafting=DraftingPolicy(),
    )


def _adapter() -> LiteLLMAdapter:
    return LiteLLMAdapter(
        proxy_url="http://proxy.test:4000",
        master_key="sk-test",
        model_alias="follow-up-model",
        completion_fn=lambda **_kwargs: None,
    )


def _candidate(
    opportunity_id: str,
    score: str,
    *,
    contact_name: str = "",
    contact_address: str = "+13125550123",
):
    return SimpleNamespace(
        id=uuid4(),
        run_at=NOW,
        primary_opportunity_id=opportunity_id,
        contact_key=f"crm:{opportunity_id}",
        contact_name=contact_name,
        contact_address=contact_address,
        channel="sms",
        reason="replied_no_answer",
        score=Decimal(score),
        other_opportunity_ids=(),
    )


def _draft(candidate, *, created_at: datetime) -> PersistedDraft:
    return PersistedDraft(
        id=uuid4(),
        candidate_id=candidate.id,
        contact_key=candidate.contact_key,
        contact_address=candidate.contact_address,
        contact_name=candidate.contact_name,
        channel=candidate.channel,
        primary_opportunity_id=candidate.primary_opportunity_id,
        opportunity_ids=(candidate.primary_opportunity_id,),
        body="Hi Jamie, checking in.\n\nFollow-up Team",
        status="pending",
        created_at=created_at,
        updated_at=created_at,
        reviewed_at=None,
    )


def test_run_review_renders_latest_pending_candidates_ranked_by_score(
    monkeypatch,
):
    from respawned.cli import review

    low = _candidate(
        "OPP-LOW",
        "10",
        contact_name="Alex Morgan",
        contact_address="+13125550456",
    )
    high = _candidate(
        "OPP-HIGH",
        "87.123456789",
        contact_name="Jamie Rivera",
        contact_address="+13125550123",
    )
    monkeypatch.setattr(
        review,
        "load_latest_candidates",
        lambda _connection: [low, high],
    )
    monkeypatch.setattr(review, "draft_candidate", lambda *_args, **_kwargs: None)
    console = _console(width=64)

    run_review(
        _Engine(),
        now=NOW,
        policy=_policy(),
        adapter=_adapter(),
        console=console,
    )

    rendered = console.export_text()
    assert "Pending follow-ups" in rendered
    assert "Contact" in rendered
    assert "Opportunity" in rendered
    assert "Reason" in rendered
    assert "Score" in rendered
    assert "Replied no answer" in rendered
    assert "87.1" in rendered
    assert "87.123456789" not in rendered
    labels = ("#", "Contact", "Opportunity", "Reason", "Score")
    heading = next(
        line for line in rendered.splitlines() if all(label in line for label in labels)
    )
    positions = [heading.index(label) for label in labels]
    assert positions == sorted(positions)
    lines = rendered.splitlines()
    customer_line = next(
        index for index, line in enumerate(lines) if "Jamie Rivera" in line
    )
    assert "+13125550123" in lines[customer_line + 1]
    assert rendered.index("OPP-HIGH") < rendered.index("OPP-LOW")
    assert rendered.index("87.1") < rendered.index("10.0")

    narrow_console = _console(width=48)
    review._show_candidate_queue([high, low], console=narrow_console)
    assert "…" not in narrow_console.export_text()


def test_candidate_queue_displays_source_text_without_interpreting_markup():
    from respawned.cli import review

    candidate = _candidate(
        "OPP-[/link]",
        "90",
        contact_name="[red]Jamie[/bold]",
        contact_address="[name]@example.com",
    )
    candidate.reason = "[/bold]"
    console = _console()

    review._show_candidate_queue([candidate], console=console)

    rendered = console.export_text()
    assert "[red]Jamie[/bold]" in rendered
    assert "OPP-[/link]" in rendered
    assert "[name]@example.com" in rendered
    assert "[/bold]" in rendered


def test_run_review_renders_draft_body_at_full_width_with_context(monkeypatch):
    from respawned.cli import review

    candidate = _candidate(
        "OPP-12345",
        "90",
        contact_name="Jamie Rivera",
        contact_address="+13125550123",
    )
    draft = _draft(candidate, created_at=NOW)
    monkeypatch.setattr(
        review,
        "load_latest_candidates",
        lambda _connection: [candidate],
    )
    monkeypatch.setattr(
        review,
        "draft_candidate",
        lambda *_args, **_kwargs: draft,
    )
    console = _console(width=48)

    run_review(
        _Engine(),
        now=NOW,
        policy=_policy(),
        adapter=_adapter(),
        console=console,
        action_prompt=lambda *_args, **_kwargs: "s",
    )

    rendered = console.export_text()
    assert "Opportunity: OPP-12345" in rendered
    assert "SMS: +13125550123" in rendered
    assert "Draft message" in rendered
    assert "Hi Jamie, checking in." in rendered
    assert "Follow-up Team" in rendered


@pytest.mark.parametrize("edit_key", ["e", "E"])
def test_run_review_renders_literal_ares_prompt_and_accepts_edit_key(
    monkeypatch,
    edit_key,
):
    from respawned.cli import review

    candidate = _candidate("OPP-EDIT", "90")
    original = _draft(candidate, created_at=NOW)
    edited_body = "Hi Jamie, updated follow-up.\n\nFollow-up Team"
    persisted_bodies: list[str] = []
    monkeypatch.setattr(
        review,
        "load_latest_candidates",
        lambda _connection: [candidate],
    )
    monkeypatch.setattr(
        review,
        "draft_candidate",
        lambda *_args, **_kwargs: original,
    )

    def fake_update_draft_message(*_args, body, **_kwargs):
        persisted_bodies.append(body)
        return replace(original, body=body)

    monkeypatch.setattr(review, "update_draft_message", fake_update_draft_message)
    console = _console(width=80)

    summary = run_review(
        _Engine(),
        now=NOW,
        policy=_policy(),
        adapter=_adapter(),
        console=console,
        action_prompt=partial(
            prompt_choice,
            stream=StringIO(f"{edit_key}\n"),
        ),
        message_prompt=lambda _draft, _console: edited_body,
    )

    rendered = console.export_text()
    prompt = "Action: [A]pprove, [R]eject, [E]dit, [S]kip:"
    assert persisted_bodies == [edited_body]
    assert summary == ReviewSummary(presented=1, skipped=1)
    assert rendered.count(prompt) == 2
    assert "[a/r/e/s]" not in rendered
    assert "(s)" not in rendered
    assert "pprove eject" not in rendered


def test_run_review_rejects_legacy_message_key(monkeypatch):
    from respawned.cli import review

    candidate = _candidate("OPP-EDIT", "90")
    draft = _draft(candidate, created_at=NOW)
    monkeypatch.setattr(
        review,
        "load_latest_candidates",
        lambda _connection: [candidate],
    )
    monkeypatch.setattr(
        review,
        "draft_candidate",
        lambda *_args, **_kwargs: draft,
    )
    monkeypatch.setattr(
        review,
        "update_draft_message",
        lambda *_args, **_kwargs: pytest.fail("legacy m key must not edit"),
    )
    console = _console(width=80)

    summary = run_review(
        _Engine(),
        now=NOW,
        policy=_policy(),
        adapter=_adapter(),
        console=console,
        action_prompt=partial(
            prompt_choice,
            stream=StringIO("m\n"),
        ),
        message_prompt=lambda *_args: pytest.fail("legacy m key must not edit"),
    )

    assert summary == ReviewSummary(presented=1, skipped=1)
    assert "Please select one of the available options" in console.export_text()


def test_run_review_uses_a_fresh_clock_value_when_approving(monkeypatch):
    from respawned.cli import review

    candidate = _candidate("OPP-CLOCK", "90")
    approval_time = NOW + timedelta(minutes=3)
    clock_values = iter((NOW, approval_time))
    observed: list[tuple[str, datetime]] = []

    monkeypatch.setattr(
        review,
        "load_latest_candidates",
        lambda _connection: [candidate],
    )

    def fake_draft_candidate(*_args, now, **_kwargs):
        observed.append(("draft", now))
        return _draft(candidate, created_at=now)

    def fake_approve_draft(*_args, now, **_kwargs):
        observed.append(("approve", now))
        return 1

    monkeypatch.setattr(review, "draft_candidate", fake_draft_candidate)
    monkeypatch.setattr(review, "approve_draft", fake_approve_draft)

    run_review(
        _Engine(),
        clock=lambda: next(clock_values),
        policy=_policy(),
        adapter=_adapter(),
        console=_console(),
        action_prompt=lambda *_args, **_kwargs: "a",
    )

    assert observed == [("draft", NOW), ("approve", approval_time)]


@pytest.mark.parametrize("final_action", ("a", "r"))
def test_run_review_binds_edit_and_final_action_to_the_displayed_copy(
    monkeypatch, final_action
):
    from respawned.cli import review

    candidate = _candidate("OPP-REVIEW-SNAPSHOT", "90")
    original = _draft(candidate, created_at=NOW)
    edited = replace(original, body="Hi Jamie, updated follow-up.")
    observed = []
    monkeypatch.setattr(review, "load_latest_candidates", lambda _connection: [candidate])
    monkeypatch.setattr(review, "draft_candidate", lambda *_args, **_kwargs: original)

    def edit(*_args, expected_review_token, **_kwargs):
        observed.append(("edit", expected_review_token))
        return edited

    def finalize(*_args, expected_review_token, **_kwargs):
        observed.append((final_action, expected_review_token))
        return 1

    monkeypatch.setattr(review, "update_draft_message", edit)
    monkeypatch.setattr(review, "approve_draft", finalize)
    monkeypatch.setattr(review, "reject_draft", finalize)
    actions = iter(("e", final_action))
    summary = run_review(
        _Engine(),
        now=NOW,
        policy=_policy(),
        adapter=_adapter(),
        console=_console(),
        action_prompt=lambda *_args, **_kwargs: next(actions),
        message_prompt=lambda *_args: edited.body,
    )

    assert observed == [
        ("edit", original.review_token),
        (final_action, edited.review_token),
    ]
    assert original.review_token != edited.review_token
    assert summary == ReviewSummary(
        presented=1, approved=int(final_action == "a"), rejected=int(final_action == "r")
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"body": "Changed copy"},
        {"contact_address": "another@example.com"},
        {"contact_key": "another-person"},
        {"channel": "email"},
        {"opportunity_ids": ("another-opportunity",)},
    ),
)
def test_review_token_binds_copy_to_its_recipient_and_opportunities(changes):
    original = _draft(_candidate("OPP-SNAPSHOT", "90"), created_at=NOW)
    changed = replace(original, **changes)

    assert changed.updated_at == original.updated_at
    assert changed.review_token != original.review_token


@pytest.mark.parametrize(
    "message", ("draft failed validation", "opportunity '[/bold]' is no longer open")
)
def test_run_review_reports_blocked_approval_without_crashing(monkeypatch, message):
    from respawned.cli import review

    candidate = _candidate("OPPORTUNITY-BLOCKED", "90")
    monkeypatch.setattr(
        review,
        "load_latest_candidates",
        lambda _connection: [candidate],
    )
    monkeypatch.setattr(
        review,
        "draft_candidate",
        lambda *_args, **_kwargs: _draft(candidate, created_at=NOW),
    )

    def blocked(*_args, **_kwargs):
        raise review.ReviewBlockedError(message)

    monkeypatch.setattr(review, "approve_draft", blocked)
    console = _console()
    summary = run_review(
        _Engine(),
        now=NOW,
        policy=_policy(),
        adapter=_adapter(),
        console=console,
        action_prompt=lambda *_args, **_kwargs: "a",
    )

    assert summary == ReviewSummary(presented=1, blocked=1)
    assert f"Blocked: {message}" in console.export_text()


def test_outbox_cooldown_uses_one_bounded_exists_query():
    from respawned.core.review import _has_outbox_reservation

    observed = {}

    class Result:
        def scalar_one(self):
            return True

    class Connection:
        def execute(self, statement, parameters):
            observed["sql"] = " ".join(str(statement).split())
            observed["parameters"] = parameters
            return Result()

    draft_id = uuid4()
    assert _has_outbox_reservation(
        Connection(),
        draft_id=draft_id,
        contact_key="crm:contact:1",
        now=NOW,
        cooldown_hours=Decimal(72),
    )
    assert "SELECT EXISTS" in observed["sql"]
    assert "created_at >" in observed["sql"]
    assert "created_at <=" not in observed["sql"]
    assert observed["parameters"] == {
        "draft_id": draft_id,
        "contact_key": "crm:contact:1",
        "now": NOW,
        "cooldown_seconds": Decimal(259200),
    }
