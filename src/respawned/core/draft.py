"""Generate guarded follow-up copy from deliberately limited context."""

from respawned.core.helpers.payload import DraftPayload
from respawned.core.helpers.validate import (
    ensure_opportunity_is_contactable,
    validate_draft,
)
from respawned.llm.adapter import ChatMessage, LiteLLMAdapter


_SYSTEM_PROMPT = """\
Write concise, highly professional follow-up copy.
Use the requested tone without explaining it. Never mention monetary values,
contact viewing activity, view counts, timestamps, tracking, or placeholders.
Return only the message body without commentary or formatting.\
"""


def build_draft_messages(payload: DraftPayload) -> list[ChatMessage]:
    """Build a prompt that exposes only the approved drafting payload."""
    open_count = payload.other_open_opportunity_count + 1
    opportunity_context = (
        "1 open opportunity"
        if open_count == 1
        else f"{open_count} open opportunities"
    )
    owner_context = (
        f"The account owner is {payload.owner_name}; mention the name naturally "
        "when it helps the message."
        if payload.owner_name
        else "No owner name is available; do not invent one."
    )
    contact_context = payload.contact_name or "not available; use a neutral greeting"
    user_prompt = "\n".join(
        (
            f"Contact name: {contact_context}",
            f"Desired tone: {payload.tone}",
            f"Opportunity context: {opportunity_context}",
            owner_context,
            f"Maximum length, including sign-off: {payload.max_characters} characters",
            f"End with this exact sign-off: {payload.sign_off}",
            "Write a natural check-in that can cover all open opportunities together.",
        )
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _apply_sign_off(body: str, sign_off: str) -> str:
    normalized_body = body.strip()
    normalized_sign_off = sign_off.strip()
    if not normalized_sign_off or normalized_body.endswith(normalized_sign_off):
        return normalized_body
    return f"{normalized_body}\n\n{normalized_sign_off}"


def draft_follow_up(
    payload: DraftPayload,
    *,
    opportunity_status: str,
    adapter: LiteLLMAdapter,
) -> str:
    """Generate, sign, and validate copy for a contactable opportunity."""
    ensure_opportunity_is_contactable(opportunity_status)
    generated_body = adapter.complete(build_draft_messages(payload))
    signed_body = _apply_sign_off(generated_body, payload.sign_off)
    return validate_draft(
        signed_body,
        max_characters=payload.max_characters,
        opportunity_status=opportunity_status,
        owner_name=payload.owner_name,
        require_owner_name=payload.require_owner_name,
    )
