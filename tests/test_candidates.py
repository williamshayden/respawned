from datetime import UTC, datetime, timedelta
from decimal import Decimal

from follow_up_engine.core.candidates import select_candidates
from follow_up_engine.core.reduce import QuoteState
from follow_up_engine.core.score import ReasonContribution, ScoredQuote


NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _state(
    quote_id: str,
    *,
    phone: str = "+13125550100",
    status: str = "open",
    last_outbound_at: datetime | None = None,
    channel: str | None = None,
) -> QuoteState:
    return QuoteState(
        quote_id=quote_id,
        status=status,
        amount=Decimal("1000"),
        customer_name=f"Customer {quote_id}",
        customer_phone=phone,
        tech_name="Sam",
        created_at=NOW - timedelta(days=10),
        quote_sent_at=NOW - timedelta(days=10),
        last_viewed_at=NOW - timedelta(days=1),
        view_days=1,
        last_replied_at=None,
        last_outbound_at=last_outbound_at,
        channel=channel,
    )


def _scored(
    quote_id: str,
    score: str,
    *,
    phone: str = "+13125550100",
) -> ScoredQuote:
    value = Decimal(score)
    contribution = ReasonContribution(
        reason="viewed_no_reply",
        score=value,
        base_factor=value,
        amount_factor=Decimal("0"),
        recency_factor=Decimal("0"),
        signal_at=NOW - timedelta(days=1),
        priority=1,
    )
    return ScoredQuote(
        quote_id=quote_id,
        customer_phone=phone,
        primary_reason=contribution.reason,
        score=value,
        base_factor=value,
        amount_factor=Decimal("0"),
        recency_factor=Decimal("0"),
        matched_reasons=(contribution,),
        amount_percentile=Decimal("0.5"),
        high_value_cutoff=Decimal("1000"),
        effective_last_outbound_at=None,
    )


def test_same_phone_yields_one_stable_candidate_with_other_open_quote():
    states = [
        _state("Q-low", phone="  +13125550123  "),
        _state("Q-high", phone="+13125550123"),
    ]
    scored = [
        _scored("Q-low", "80", phone="+13125550123"),
        _scored("Q-high", "90", phone="+13125550123"),
    ]

    [candidate] = select_candidates(
        states,
        scored,
        NOW,
        Decimal("72"),
    )
    [same_opportunity_later] = select_candidates(
        states,
        scored,
        NOW + timedelta(hours=1),
        Decimal("72"),
    )

    assert candidate.primary_quote_id == "Q-high"
    assert candidate.customer_name == "Customer Q-high"
    assert candidate.customer_phone == "+13125550123"
    assert candidate.other_quote_ids == ("Q-low",)
    assert candidate.id == same_opportunity_later.id


def test_recent_contact_on_terminal_sibling_suppresses_open_quote():
    phone = "+13125550123"
    states = [
        _state("Q-open", phone=phone),
        _state(
            "Q-accepted",
            phone=f" {phone} ",
            status="accepted",
            last_outbound_at=NOW - timedelta(hours=1),
        ),
    ]

    assert select_candidates(
        states,
        [_scored("Q-open", "90", phone=phone)],
        NOW,
        Decimal("72"),
    ) == []


def test_candidate_defaults_missing_primary_quote_channel_to_sms():
    [candidate] = select_candidates(
        [_state("Q-channel")],
        [_scored("Q-channel", "90")],
        NOW,
        Decimal("72"),
    )

    assert candidate.channel == "sms"


def test_candidate_uses_primary_quote_email_channel():
    [candidate] = select_candidates(
        [_state("Q-channel", channel="email")],
        [_scored("Q-channel", "90")],
        NOW,
        Decimal("72"),
    )

    assert candidate.channel == "email"


def test_candidate_defaults_invalid_primary_quote_channel_to_sms():
    [candidate] = select_candidates(
        [_state("Q-channel", channel="fax")],
        [_scored("Q-channel", "90")],
        NOW,
        Decimal("72"),
    )

    assert candidate.channel == "sms"
