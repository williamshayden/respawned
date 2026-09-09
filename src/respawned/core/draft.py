"""Generate guarded follow-up copy from deliberately limited context."""

from respawned.core.helpers.payload import DraftPayload
from respawned.core.helpers.validate import (
    ensure_quote_is_contactable,
    validate_draft,
)
from respawned.llm.adapter import ChatMessage, LiteLLMAdapter


_SYSTEM_PROMPT = """\
Write concise, highly professional customer follow-up copy for a service business.
Use the requested tone without explaining it. Never mention prices, quote amounts,
customer viewing activity, view counts, timestamps, tracking, or placeholders.
Return only the message body without commentary or formatting.\
"""


def build_draft_messages(payload: DraftPayload) -> list[ChatMessage]:
    """Build a prompt that exposes only the approved drafting payload."""
    open_quote_count = payload.other_open_quote_count + 1
    quote_context = (
        "1 open quote"
        if open_quote_count == 1
        else f"{open_quote_count} open quotes"
    )
    tech_context = (
        f"The service technician is {payload.tech_name}; mention the name naturally "
        "when it helps the message."
        if payload.tech_name
        else "No technician name is available; do not invent one."
    )
    user_prompt = "\n".join(
        (
            f"Customer name: {payload.customer_name}",
            f"Desired tone: {payload.tone}",
            f"Quote context: {quote_context}",
            tech_context,
            f"Maximum length, including sign-off: {payload.max_characters} characters",
            f"End with this exact sign-off: {payload.sign_off}",
            "Write a natural check-in that can cover all open quotes together.",
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
    quote_status: str,
    adapter: LiteLLMAdapter,
) -> str:
    """Generate, sign, and validate copy for a contactable quote."""
    ensure_quote_is_contactable(quote_status)
    generated_body = adapter.complete(build_draft_messages(payload))
    signed_body = _apply_sign_off(generated_body, payload.sign_off)
    return validate_draft(
        signed_body,
        max_characters=payload.max_characters,
        quote_status=quote_status,
        tech_name=payload.tech_name,
        require_tech_name=payload.require_tech_name,
    )
