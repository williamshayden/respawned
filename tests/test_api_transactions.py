"""Assert wire-level responses follow transaction completion, without a database."""

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from respawned.api import app as api_module


@pytest.mark.parametrize("commit_fails", [False, True])
def test_ingestion_commits_before_sending_response(monkeypatch, commit_fails):
    events = []
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "transaction-test")

    @contextmanager
    def transaction():
        yield object()
        events.append("commit")
        if commit_fails:
            raise RuntimeError("simulated commit failure")

    monkeypatch.setitem(
        api_module.app.dependency_overrides, api_module.get_api_engine,
        lambda: SimpleNamespace(begin=transaction)
    )
    monkeypatch.setattr(
        api_module,
        "ingest_records",
        lambda *_args, **_kwargs: SimpleNamespace(
            opportunities_upserted=0, activities_inserted=1
        ),
    )

    async def request():
        body = json.dumps(
            {
                "activities": [
                    {
                        "id": "activity-1",
                        "type": "contact_replied",
                        "opportunity_id": "opportunity-1",
                        "occurred_at": "2026-09-08T12:00:00Z",
                    }
                ]
            }
        ).encode()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/ingest",
            "raw_path": b"/v1/ingest",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json"), (b"authorization", b"Bearer transaction-test")],
            "client": ("test", 123),
            "server": ("test", 80),
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            events.append(message)

        await api_module.app(scope, receive, send)

    if commit_fails:
        # Starlette sends a 500, then re-raises for the server to log the failure.
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            asyncio.run(request())
    else:
        asyncio.run(request())

    assert events[0] == "commit"
    responses = [event for event in events[1:] if event["type"] == "http.response.start"]
    assert len(responses) == 1
    assert responses[0]["status"] == (500 if commit_fails else 200)
    body = b"".join(
        event["body"] for event in events[1:] if event["type"] == "http.response.body"
    )
    if commit_fails:
        assert body == b"Internal Server Error"
    else:
        assert json.loads(body) == {
            "opportunities_upserted": 0,
            "activities_inserted": 1,
        }
