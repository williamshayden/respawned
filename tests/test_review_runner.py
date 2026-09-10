"""Interactive review renders API snapshots and never makes a decision implicitly."""

from copy import deepcopy
from functools import partial
from io import StringIO
from uuid import uuid4

import pytest
from rich.console import Console

from respawned.client import APIError
from respawned.cli.review import ReviewSummary, run_review
from respawned.cli.ui import prompt_choice


def console(width=100):
    return Console(file=StringIO(), record=True, force_terminal=False, no_color=True, width=width)


def candidate(key="one", **changes):
    return {"id": str(uuid4()), "primary_opportunity_id": key, "contact_name": "Jamie",
            "contact_address": "jamie@example.com", "channel": "email", "contact_key": f"person:{key}",
            "reason": "replied_no_answer", "score": "90", "other_opportunity_ids": [], **changes}


def draft(row, **changes):
    return {**row, "id": str(uuid4()), "candidate_id": row["id"], "body": "Hi Jamie, checking in.",
            "opportunity_ids": [row["primary_opportunity_id"]], "status": "pending",
            "review_token": "a" * 64, "outbox_id": None, "validation_errors": [], **changes}


class Client:
    def __init__(self, candidates=None):
        self.candidates = candidates if candidates is not None else [candidate()]
        self.drafts = {row["id"]: draft(row) for row in self.candidates}
        self.calls = []
        self.failures = {}

    def call(self, name, *args):
        self.calls.append((name, *args))
        error = self.failures.get(name)
        if error is not None:
            raise error

    def sync(self, limit=10):
        self.call("sync", limit)
        return {"candidate_count": len(self.candidates), "inserted_count": 0, "dry_run": False, "run_id": "saved"}

    def queue(self):
        self.call("queue")
        return {"items": self.candidates, "has_more": False}

    def draft_candidate(self, identity):
        self.call("draft", identity)
        return deepcopy(self.drafts[identity])

    def edit_draft(self, identity, body, review_token):
        self.call("edit", identity, body, review_token)
        return {"id": identity, "body": body, "review_token": "b" * 64, "status": "pending"}

    def approve_draft(self, identity, review_token):
        self.call("approve", identity, review_token)
        return {"status": "approved", "outbox_id": 1}

    def reject_draft(self, identity, review_token):
        self.call("reject", identity, review_token)
        return {"status": "rejected"}


def test_review_preserves_server_queue_order_and_literal_source_text():
    first = candidate("first[/link]", contact_name="[red]Jamie[/bold]", score="1")
    second = candidate("second", contact_name="", contact_address="[name]@example.com", score="99")
    client, output = Client([first, second]), console()
    summary = run_review(client, limit=2, console=output, action_prompt=lambda *_args, **_kwargs: "s")
    rendered = output.export_text()
    assert rendered.index("first[/link]") < rendered.index("second")
    assert "[red]Jamie[/bold]" in rendered and "[name]@example.com" in rendered
    assert "Pending follow-ups" in rendered and "Replied no answer" in rendered
    assert summary == ReviewSummary(presented=2, skipped=2)
    assert client.calls[:2] == [("sync", 2), ("queue",)]


def test_empty_queue_does_not_request_a_draft_or_prompt():
    client = Client([])
    assert run_review(client, console=console(), action_prompt=lambda *_args, **_kwargs: pytest.fail("Empty queue prompted")) == ReviewSummary()
    assert client.calls == [("sync", 10), ("queue",)]


def test_review_displays_the_persisted_recipient_and_body():
    client, output = Client(), console(48)
    value = next(iter(client.drafts.values()))
    value.update(primary_opportunity_id="source/record", contact_address="reviewed@example.com", body="Hi Jamie, checking in.\n\nSigned copy")
    run_review(client, console=output, action_prompt=lambda *_args, **_kwargs: "s")
    text = output.export_text()
    assert "Record: source/record" in text
    assert "EMAIL: reviewed@example.com" in text
    assert "Hi Jamie, checking in." in text and "Signed copy" in text


@pytest.mark.parametrize("edit_key", ["e", "E"])
def test_review_literal_prompt_accepts_edit_without_implicit_approval(edit_key):
    client, output = Client(), console()
    summary = run_review(client, console=output, action_prompt=partial(prompt_choice, stream=StringIO(f"{edit_key}\ns\n")),
                         message_prompt=lambda *_args: "Hi Jamie, Thursday works.")
    rendered = output.export_text()
    assert rendered.count("Action: [A]pprove, [R]eject, [E]dit, [S]kip:") == 2
    assert "[a/r/e/s]" not in rendered and "(s)" not in rendered
    assert summary == ReviewSummary(presented=1, skipped=1)
    assert len([call for call in client.calls if call[0] == "edit"]) == 1
    assert not [call for call in client.calls if call[0] in {"approve", "reject"}]


@pytest.mark.parametrize("final_action", ["a", "r"])
def test_review_binds_edit_and_final_action_to_the_displayed_server_version(final_action):
    client, output = Client(), console()
    original = next(iter(client.drafts.values()))
    actions = iter(("e", final_action))

    def choose(*_args, **_kwargs):
        action = next(actions)
        if action != "e":
            assert "Hi Jamie, Friday works." in output.export_text()
        return action

    result = run_review(client, console=output, action_prompt=choose,
                        message_prompt=lambda *_args: "Hi Jamie, Friday works.")
    decisions = [call for call in client.calls if call[0] in {"edit", "approve", "reject"}]
    assert decisions == [("edit", original["id"], "Hi Jamie, Friday works.", "a" * 64),
                         ("approve" if final_action == "a" else "reject", original["id"], "b" * 64)]
    assert result == ReviewSummary(presented=1, approved=int(final_action == "a"), rejected=int(final_action == "r"))


@pytest.mark.parametrize("operation", ["draft", "edit", "approve"])
@pytest.mark.parametrize("error", [APIError("Changed since review", 409), APIError("Lost response", ambiguous=True)])
def test_conflict_or_unknown_write_is_never_retried_or_implicitly_reapproved(operation, error):
    client, output = Client(), console()
    client.failures[operation] = error
    actions = iter(("e", "a") if operation == "edit" else ("a",))
    result = run_review(client, console=output, action_prompt=lambda *_args, **_kwargs: next(actions),
                        message_prompt=lambda *_args: "Updated copy")
    assert result.blocked == 1 and result.approved == 0
    assert len([call for call in client.calls if call[0] == operation]) == 1
    if operation != "approve":
        assert not [call for call in client.calls if call[0] == "approve"]
    rendered = output.export_text()
    assert "Reopen" in rendered or "Blocked" in rendered


def test_invalid_edit_can_be_corrected_before_approval():
    client, output = Client(), console()
    actions = iter(("e", "e", "a"))
    messages = iter(("", "Hi Jamie, Friday works."))
    original_edit = client.edit_draft

    def edit(identity, body, token):
        if not body:
            client.call("edit", identity, body, token)
            raise APIError("Draft cannot be blank", 422)
        return original_edit(identity, body, token)

    client.edit_draft = edit
    summary = run_review(client, console=output, action_prompt=lambda *_args, **_kwargs: next(actions),
                         message_prompt=lambda *_args: next(messages))
    assert summary == ReviewSummary(presented=1, approved=1)
    assert [call[-1] for call in client.calls if call[0] == "edit"] == ["a" * 64, "a" * 64]
    assert [call[-1] for call in client.calls if call[0] == "approve"] == ["b" * 64]


@pytest.mark.parametrize("during_edit", [False, True])
@pytest.mark.parametrize("interruption", [EOFError, KeyboardInterrupt])
def test_interrupted_review_skips_without_writing(during_edit, interruption):
    client = Client()

    def cancel(*_args, **_kwargs):
        raise interruption

    summary = run_review(client, console=console(), action_prompt=(lambda *_args, **_kwargs: "e") if during_edit else cancel,
                         message_prompt=cancel)
    assert summary == ReviewSummary(presented=1, skipped=1)
    assert not [call for call in client.calls if call[0] in {"edit", "approve", "reject"}]
