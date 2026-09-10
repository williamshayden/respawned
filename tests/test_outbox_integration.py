"""One persisted review reservation across HTTP, CLI export, and ingested evidence."""

import csv
import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx

from http_engine import serve_http
import pytest
from sqlalchemy import create_engine, text

from respawned.api import app as api_module, ui
from respawned.cli.common import DEFAULT_POLICY_PATH
from respawned.core.outbox import CSV_FIELDS, render_outbox_csv
from respawned.core.policy import load_policy
from respawned.core.workspaces import create_workspace
from respawned.db.helpers.pg_connect import create_tables
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
HEADERS = {"Authorization": "Bearer outbox-integration-operator"}


@pytest.fixture
def persisted_outbox_api(postgres_engine, monkeypatch):
    # The CLI is a different process: use committed data in an owned schema,
    # never the transaction-only fixture or the developer's staging data.
    schema = "respawned_outbox_" + uuid4().hex
    engine = create_engine(postgres_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    previous = dict(api_module.app.dependency_overrides)
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "outbox-integration-operator")
    state = SimpleNamespace(engine=engine, schema=schema, now=NOW, calls=0)

    def completion(**_kwargs):
        state.calls += 1
        return {"choices": [{"message": {"content": "Hi Avery, when would you like to speak?"}}]}

    state.adapter = LiteLLMAdapter("http://unused.test", "unused", "stub", completion_fn=completion)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        create_tables(engine)
        api_module.app.dependency_overrides[api_module.get_api_engine] = lambda: engine
        api_module.app.dependency_overrides[api_module.get_workflow_policy] = lambda: load_policy(DEFAULT_POLICY_PATH)
        api_module.app.dependency_overrides[api_module.get_workflow_clock] = lambda: lambda: state.now
        api_module.app.dependency_overrides[ui.get_draft_adapter_factory] = lambda: lambda: state.adapter
        with serve_http(api_module.app) as base_url:
            state.url = base_url
            with httpx.Client(base_url=base_url, headers=HEADERS, timeout=10, trust_env=False) as client:
                state.client = client
                yield state
    finally:
        api_module.app.dependency_overrides.clear()
        api_module.app.dependency_overrides.update(previous)
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def _post(state, path, payload, status=200):
    response = state.client.post(path, headers=HEADERS, json=payload)
    assert response.status_code == status, response.text
    return response.json()


def _approved(state, key="one", kind="community"):
    _post(state, "/v1/ui/import", {"opportunities": [{
        "id": key, "contact_key": f"person:{key}", "contact_name": "Avery",
        "contact_email": f"{key}@example.com", "status": "open", "kind": kind,
        "created_at": (NOW - timedelta(days=5)).isoformat(),
    }], "activities": [{
        "id": key + ":reply", "opportunity_id": key, "type": "contact_replied",
        "direction": "inbound", "channel": "email", "classification": "human",
        "occurred_at": (NOW - timedelta(hours=1)).isoformat(),
    }]})
    draft = _post(state, f"/v1/ui/records/{key}/draft", {})
    edited = _post(state, f"/v1/ui/drafts/{draft['id']}/edit", {
        "body": 'Hi Avery, Thursday works.\nWould "after lunch" suit you?',
        "review_token": draft["review_token"],
    })
    stale = _post(state, f"/v1/ui/drafts/{draft['id']}/approve", {
        "review_token": draft["review_token"],
    }, status=409)
    assert "changed since it was shown" in stale["detail"]
    assert all(item["draft_id"] != draft["id"]
               for item in state.client.get("/v1/outbox").json()["items"])
    approved = _post(state, f"/v1/ui/drafts/{draft['id']}/approve", {
        "review_token": edited["review_token"],
    })
    assert _post(state, f"/v1/ui/drafts/{draft['id']}/approve", {
        "review_token": edited["review_token"],
    }) == approved
    return approved


def test_persisted_review_cli_api_exports_and_outbound_evidence(persisted_outbox_api, tmp_path):
    state = persisted_outbox_api
    approved = _approved(state)
    canonical = state.client.get("/v1/outbox").json()["items"]
    assert len(canonical) == 1
    assert canonical[0]["id"] == approved["outbox_id"]
    assert canonical[0]["body"] == approved["body"]
    assert canonical[0]["status"] == "pending" and canonical[0]["sent_at"] is None
    assert canonical[0]["authorization_mode"] == "human"
    ui_item = state.client.get("/v1/ui/outbox", headers=HEADERS).json()["items"][0]
    assert {field: ui_item[field] for field in CSV_FIELDS} == canonical[0]
    for path in ("/v1/outbox/export", "/v1/ui/outbox/export"):
        exported = state.client.get(path, headers=HEADERS)
        assert exported.json() == {"items": canonical}
        assert exported.headers["cache-control"] == "no-store"
        assert state.client.get(path, headers={"Authorization": ""}).status_code == 401

    destination = tmp_path / "outside-checkout" / "outbox.csv"
    environment = {**os.environ, "DB_HOST": "never-connect.invalid", "DB_PORT": "invalid-db-port",
                   "RESPAWNED_API_URL": state.url, "RESPAWNED_REVIEW_TOKEN": "outbox-integration-operator",
                   "RESPAWNED_OUTBOX_TOKEN": "", "RESPAWNED_PROCESS_TOKEN": ""}
    result = subprocess.run(
        [sys.executable, "-m", "respawned", "outbox", "--path", str(destination)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = destination.read_bytes()
    for path in ("/v1/outbox/export?format=csv", "/v1/ui/outbox/export?format=csv"):
        response = state.client.get(path, headers=HEADERS)
        assert response.status_code == 200 and response.content == expected
        assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(expected.decode("utf-8"))))
    assert tuple(rows[0]) == CSV_FIELDS and rows[0]["body"] == approved["body"]
    inbox = state.client.get("/v1/ui/inbox", headers=HEADERS).json()
    assert inbox["total"] == 1 and inbox["items"][0]["pending_outbox_count"] == 1

    # Export is read-only. The sender's actual outbound event is separate,
    # immutable evidence; approval/export alone cannot resolve the reply.
    state.now = NOW + timedelta(minutes=2)
    event = {"id": "sender:message:one", "opportunity_id": "one", "type": "message_sent",
             "direction": "outbound", "channel": "email", "classification": "human",
             "occurred_at": (NOW + timedelta(minutes=1)).isoformat(),
             "summary": approved["body"]}
    assert _post(state, "/v1/ingest", {"activities": [event]})["activities_inserted"] == 1
    assert _post(state, "/v1/ingest", {"activities": [event]})["activities_inserted"] == 0
    _post(state, "/v1/ingest", {"activities": [{**event, "summary": "A conflicting source fact"}]}, status=409)
    assert state.client.get("/v1/inbox").json()["total"] == 0
    assert state.client.get("/v1/ui/inbox", headers=HEADERS).json()["total"] == 0
    assert _post(state, "/v1/ui/sync", {})["candidate_count"] == 0
    with state.engine.connect() as connection:
        assert connection.execute(text("SELECT summary FROM activities WHERE id = 'sender:message:one'")).scalar_one() == approved["body"]
        assert connection.execute(text("SELECT count(*) FROM outbox")).scalar_one() == 1
    # Ingestion does not guess which outbox reservation an external send used.
    assert state.client.get("/v1/outbox").json()["items"] == canonical
    assert state.calls == 1


def test_workspace_outbox_export_uses_whole_persisted_reservations(persisted_outbox_api):
    state = persisted_outbox_api
    approved = _approved(state)
    _post(state, "/v1/ui/import", {"opportunities": [{
        "id": "related", "contact_key": "person:one", "contact_email": "one@example.com",
        "status": "open", "kind": "partnership", "created_at": NOW.isoformat(),
    }]})
    with state.engine.begin() as connection:
        # Represent an existing approved multi-record snapshot. Matching its
        # secondary record must retain the complete approved message and IDs.
        connection.execute(text("UPDATE outbox SET opportunity_ids = ARRAY['one', 'related']"))
        matching = create_workspace(connection, name="Partnerships", description="", kinds=["partnership"])
        empty = create_workspace(connection, name="Other", description="", kinds=["unmatched"])
    for workspace, count in ((matching, 1), (empty, 0)):
        suffix = "?workspace_id=" + str(workspace["id"])
        items = state.client.get("/v1/ui/outbox" + suffix, headers=HEADERS).json()["items"]
        exported = state.client.get("/v1/outbox/export" + suffix, headers=HEADERS).json()["items"]
        assert len(items) == len(exported) == count
        if count:
            assert exported[0]["opportunity_ids"] == ["one", "related"]
            assert exported[0]["body"] == approved["body"]
    for path in ("/v1/ui/outbox", "/v1/outbox/export", "/v1/ui/outbox/export"):
        assert state.client.get(path + "?workspace_id=" + str(uuid4()), headers=HEADERS).status_code == 404


@pytest.mark.parametrize("unsafe", ["=1+1", "+15550000001", "-1+2", "@SUM(1)",
                                   "\tformula", "\rformula", "\nformula", "  =1+1"])
def test_csv_protects_spreadsheet_cells_without_mutating_raw_snapshot(unsafe):
    row = dict.fromkeys(CSV_FIELDS, unsafe)
    row.update(id=1, draft_id=uuid4(), opportunity_ids=['record,with"quotes'],
               created_at=NOW, sent_at=None)
    parsed = list(csv.DictReader(io.StringIO(render_outbox_csv([row]))))[0]
    for field in ("contact_key", "contact_address", "contact_name", "body"):
        assert parsed[field] == "'" + unsafe
        assert row[field] == unsafe
    assert parsed["opportunity_ids"] == '["record,with\\"quotes"]'


def test_empty_csv_has_the_full_contract():
    assert list(csv.reader(io.StringIO(render_outbox_csv([])))) == [list(CSV_FIELDS)]


def test_cli_json_pending_batch_matches_api_and_retains_complete_history(persisted_outbox_api, tmp_path):
    state = persisted_outbox_api
    environment = {**os.environ, "DB_HOST": "never-connect.invalid", "DB_PORT": "invalid-db-port",
                   "RESPAWNED_API_URL": state.url, "RESPAWNED_REVIEW_TOKEN": "outbox-integration-operator",
                   "RESPAWNED_OUTBOX_TOKEN": "", "RESPAWNED_PROCESS_TOKEN": ""}
    # Invalid database settings prove the CLI uses only its authenticated HTTP connection.

    def cli(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "respawned", "outbox", *arguments],
            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    for arguments in (("--json",), ("--pending", "--json")):
        assert json.loads(cli(*arguments)) == {"items": [], "has_more": False}
    ids = [_approved(state, key)["outbox_id"] for key in ("human", "automatic", "legacy", "sent", "failed")]
    exact_body = '=literal follow-up\r\nCafé — “Thursday”, 14:00.\n'
    with state.engine.begin() as connection:
        connection.execute(text("UPDATE outbox SET body = :body WHERE id = :id"),
                           {"body": exact_body, "id": ids[0]})
        for identity, mode in ((ids[1], "automatic"), (ids[2], "legacy_unknown")):
            connection.execute(text("UPDATE outbox SET authorization_mode = :mode WHERE id = :id"),
                               {"mode": mode, "id": identity})
        for identity, status in ((ids[3], "sent"), (ids[4], "failed")):
            connection.execute(text("UPDATE outbox SET status = :status WHERE id = :id"),
                               {"status": status, "id": identity})

    all_items = state.client.get("/v1/outbox").json()["items"]
    history = json.loads(cli("--json"))
    assert history == {"items": all_items, "has_more": False}
    assert len(history["items"]) == 5
    assert history["items"][0]["body"] == exact_body
    for limit in (1, 2, 200):
        expected = state.client.get(f"/v1/outbox/pending?limit={limit}", headers=HEADERS).json()
        batch = json.loads(cli("--pending", "--json", "--limit", str(limit)))
        assert batch == expected
        assert [item["id"] for item in batch["items"]] == ids[:min(limit, 2)]
        assert batch["has_more"] is (limit == 1)

    # A confirmed receipt removes the first batch item. Polling has no cursor,
    # and an unbounded history export still contains the acknowledged message.
    sent_at = NOW + timedelta(minutes=1)
    state.now = sent_at
    _post(state, f"/v1/outbox/{ids[0]}/receipt", {
        "sender": "cli-test", "provider_message_id": "confirmed-one", "sent_at": sent_at.isoformat(),
    })
    pending = json.loads(cli("--pending", "--json", "--limit", "1"))
    assert pending == state.client.get("/v1/outbox/pending?limit=1", headers=HEADERS).json()
    assert [item["id"] for item in pending["items"]] == [ids[1]]
    assert pending["has_more"] is False
    history = json.loads(cli("--json"))
    assert history["items"][0]["sent_at"] == sent_at.isoformat().replace("+00:00", "Z")
    assert history["items"][0]["status"] == "sent"
    destination = tmp_path / "all-statuses.csv"
    cli("--path", str(destination))
    with destination.open(newline="") as output:
        assert len(list(csv.DictReader(output))) == 5
    assert state.client.get("/v1/outbox").json()["items"] == history["items"]
