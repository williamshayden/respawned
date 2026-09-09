from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from respawned.core.candidates import select_candidates
from respawned.core.context import BusinessContext
from respawned.core.domain import OpportunityState
from respawned.core.policy import Policy, RankingPolicy
from respawned.core.score import ScoredOpportunity, ScoredReason


NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
POLICY = Policy(
    business_context=BusinessContext("UTC"),
    cooldown_hours=Decimal("72"),
    dead_after_days=45,
    ranking=RankingPolicy(Decimal("0"), Decimal("0")),
    reasons={},
)


def _state(
    opportunity_id: str,
    *,
    contact_key: str | None = "contact-1",
    phone: str | None = "+13125550100",
    email: str | None = None,
    preferred_channel: str | None = None,
    status: str = "open",
    last_outbound_at: datetime | None = None,
    created_at: datetime = NOW - timedelta(days=10),
) -> OpportunityState:
    return OpportunityState(
        opportunity_id=opportunity_id,
        status=status,
        value=Decimal("1000"),
        contact_key=contact_key,
        contact_name=f"Contact {opportunity_id}",
        contact_phone=phone,
        contact_email=email,
        owner_name="Sam",
        created_at=created_at,
        last_viewed_at=NOW - timedelta(days=1),
        last_outbound_at=last_outbound_at,
        preferred_channel=preferred_channel,
    )


def _scored(opportunity_id: str, score: str) -> ScoredOpportunity:
    value = Decimal(score)
    reason = ScoredReason(
        reason="viewed_no_reply",
        signal_strength=Decimal("1"),
        signal_at=NOW - timedelta(days=1),
        score=value,
    )
    return ScoredOpportunity(
        opportunity_id=opportunity_id,
        score=value,
        value_percentile=Decimal("0.5"),
        reasons=(reason,),
    )


def test_same_contact_yields_one_stable_candidate_with_other_open_opportunity():
    states = [_state("low"), _state("high", phone="+13125550123")]
    scored = [_scored("low", "80"), _scored("high", "90")]

    [candidate] = select_candidates(states, scored, NOW, POLICY)
    [later] = select_candidates(
        states, scored, NOW + timedelta(hours=1), POLICY
    )

    assert candidate.primary_opportunity_id == "high"
    assert candidate.contact_key == "contact-1"
    assert candidate.contact_name == "Contact high"
    assert candidate.contact_address == "+13125550123"
    assert candidate.other_opportunity_ids == ("low",)
    # Rename must preserve persisted candidate identities.
    assert str(candidate.id) == "53febf49-2706-547d-8f7f-b58f2f48f8a0"
    assert candidate.id == later.id


def test_recent_contact_on_terminal_sibling_suppresses_open_opportunity():
    states = [
        _state("open"),
        _state(
            "accepted",
            status="accepted",
            email="person@example.com",
            last_outbound_at=NOW - timedelta(hours=1),
        ),
    ]

    assert select_candidates(
        states, [_scored("open", "90")], NOW, POLICY
    ) == []


def test_email_only_contact_becomes_email_candidate():
    state = _state("email", phone=None, email="person@example.com")

    [candidate] = select_candidates(
        [state], [_scored("email", "90")], NOW, POLICY
    )

    assert (candidate.channel, candidate.contact_address) == (
        "email",
        "person@example.com",
    )


def test_candidate_honors_preferred_email_and_falls_back_to_sms():
    preferred = _state(
        "preferred",
        email="person@example.com",
        preferred_channel="email",
    )
    fallback = _state("fallback", email=None, preferred_channel="email")

    [email_candidate] = select_candidates(
        [preferred], [_scored("preferred", "90")], NOW, POLICY
    )
    [sms_candidate] = select_candidates(
        [fallback], [_scored("fallback", "90")], NOW, POLICY
    )

    assert email_candidate.channel == "email"
    assert sms_candidate.channel == "sms"


def test_missing_contact_identity_or_destination_is_suppressed():
    no_key = _state("no-key", contact_key=None)
    no_route = _state("no-route", phone=None, email=None)

    assert select_candidates(
        [no_key], [_scored("no-key", "90")], NOW, POLICY
    ) == []
    assert select_candidates(
        [no_route], [_scored("no-route", "90")], NOW, POLICY
    ) == []


def test_destination_is_never_used_to_group_contacts():
    states = [
        _state("one", contact_key="contact-1"),
        _state("two", contact_key="contact-2"),
    ]

    candidates = select_candidates(
        states,
        [_scored("one", "90"), _scored("two", "80")],
        NOW,
        POLICY,
    )

    assert [candidate.contact_key for candidate in candidates] == [
        "contact-1",
        "contact-2",
    ]


def test_candidate_identity_tracks_route_but_normalizes_address_formatting():
    state = _state("route", phone="+1 (312) 555-0100")
    [original] = select_candidates(
        [state], [_scored("route", "90")], NOW, POLICY
    )
    [reformatted] = select_candidates(
        [replace(state, contact_phone="13125550100")],
        [_scored("route", "90")],
        NOW,
        POLICY,
    )
    [email] = select_candidates(
        [
            replace(
                state,
                contact_email="person@example.com",
                preferred_channel="email",
            )
        ],
        [_scored("route", "90")],
        NOW,
        POLICY,
    )

    assert original.id == reformatted.id
    assert email.id != original.id


def test_dead_or_future_open_siblings_are_not_mentioned():
    primary = _state("primary")
    dead = _state("dead", created_at=NOW - timedelta(days=45))
    future = _state("future", created_at=NOW + timedelta(days=1))

    [candidate] = select_candidates(
        [primary, dead, future],
        [_scored("primary", "90")],
        NOW,
        POLICY,
    )

    assert candidate.primary_opportunity_id == "primary"
    assert candidate.other_opportunity_ids == ()
