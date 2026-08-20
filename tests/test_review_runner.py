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

from follow_up_engine.cli.review import PersistedDraft, ReviewSummary, run_review
from follow_up_engine.cli.ui import prompt_choice
from follow_up_engine.core.context import BusinessContext
from follow_up_engine.core.score import DraftingPolicy, Policy, ReasonPolicy
from follow_up_engine.llm.adapter import LiteLLMAdapter


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
        cooldown_hours=Decimal("72"),
        dead_after_days=45,
        high_pct=Decimal("0.75"),
        reasons={
            "replied_no_answer": ReasonPolicy(
                base=Decimal("1"),
                amount_weight=Decimal("0"),
                recency_weight=Decimal("0"),
                priority=1,
                recency_days=Decimal("7"),
                tone="warm",
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
    quote_id: str,
    score: str,
    *,
    customer_name: str = "",
    customer_phone: str = "+13125550123",
):
    return SimpleNamespace(
        id=uuid4(),
        run_at=NOW,
        primary_quote_id=quote_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        reason="replied_no_answer",
        score=Decimal(score),
        other_quote_ids=(),
    )


def _draft(candidate, *, created_at: datetime) -> PersistedDraft:
    return PersistedDraft(
        id=uuid4(),
        candidate_id=candidate.id,
        customer_phone=candidate.customer_phone,
        primary_quote_id=candidate.primary_quote_id,
        quote_ids=(candidate.primary_quote_id,),
        body="Hi Jamie, checking in.\n\nService Team",
        status="pending",
        created_at=created_at,
        updated_at=created_at,
        reviewed_at=None,
    )


def test_run_review_renders_latest_pending_candidates_ranked_by_score(
    monkeypatch,
):
    import follow_up_engine.cli.review as review

    low = _candidate(
        "QUOTE-LOW",
        "10",
        customer_name="Alex Morgan",
        customer_phone="+13125550456",
    )
    high = _candidate(
        "QUOTE-HIGH",
        "87.123456789",
        customer_name="Jamie Rivera",
        customer_phone="+13125550123",
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
    assert "Customer" in rendered
    assert "Quote" in rendered
    assert "Reason" in rendered
    assert "Score" in rendered
    assert "Replied no answer" in rendered
    assert "87.1" in rendered
    assert "87.123456789" not in rendered
    labels = ("#", "Customer", "Quote", "Reason", "Score")
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
    assert rendered.index("QUOTE-HIGH") < rendered.index("QUOTE-LOW")
    assert rendered.index("87.1") < rendered.index("10.0")

    narrow_console = _console(width=48)
    review._show_candidate_queue([high, low], console=narrow_console)
    assert "…" not in narrow_console.export_text()


def test_run_review_renders_draft_body_at_full_width_with_context(monkeypatch):
    import follow_up_engine.cli.review as review

    candidate = _candidate(
        "QUOTE-12345",
        "90",
        customer_name="Jamie Rivera",
        customer_phone="+13125550123",
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
    assert "Quote: QUOTE-12345" in rendered
    assert "Phone: +13125550123" in rendered
    assert "Draft message" in rendered
    assert "Hi Jamie, checking in." in rendered
    assert "Service Team" in rendered


@pytest.mark.parametrize("edit_key", ["e", "E"])
def test_run_review_renders_literal_ares_prompt_and_accepts_edit_key(
    monkeypatch,
    edit_key,
):
    import follow_up_engine.cli.review as review

    candidate = _candidate("QUOTE-EDIT", "90")
    original = _draft(candidate, created_at=NOW)
    edited_body = "Hi Jamie, updated follow-up.\n\nService Team"
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
    import follow_up_engine.cli.review as review

    candidate = _candidate("QUOTE-EDIT", "90")
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
    import follow_up_engine.cli.review as review

    candidate = _candidate("QUOTE-CLOCK", "90")
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
