from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
from uuid import uuid4

from rich.console import Console

from follow_up_engine.cli.review import PersistedDraft, run_review
from follow_up_engine.core.candidates import Candidate
from follow_up_engine.core.context import BusinessContext
from follow_up_engine.core.score import DraftingPolicy, Policy, ReasonPolicy
from follow_up_engine.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


class _Engine:
    def begin(self):
        return nullcontext(object())


def _console() -> Console:
    return Console(
        file=StringIO(),
        record=True,
        force_terminal=False,
        no_color=True,
        width=120,
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


def _candidate(quote_id: str, score: str) -> Candidate:
    return Candidate(
        id=uuid4(),
        run_at=NOW,
        primary_quote_id=quote_id,
        customer_phone=f"+1312555{score.zfill(4)}",
        reason="replied_no_answer",
        score=Decimal(score),
        other_quote_ids=(),
    )


def _draft(candidate: Candidate, *, created_at: datetime) -> PersistedDraft:
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

    low = _candidate("QUOTE-LOW", "10")
    high = _candidate("QUOTE-HIGH", "87")
    monkeypatch.setattr(review, "load_latest_candidates", lambda _connection: [low, high])
    monkeypatch.setattr(review, "draft_candidate", lambda *_args, **_kwargs: None)
    console = _console()

    run_review(
        _Engine(),
        now=NOW,
        policy=_policy(),
        adapter=_adapter(),
        console=console,
    )

    rendered = console.export_text()
    assert "Pending follow-ups" in rendered
    assert "Rank" in rendered
    assert rendered.index("QUOTE-HIGH") < rendered.index("QUOTE-LOW")
    assert rendered.index("87") < rendered.index("10")


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
