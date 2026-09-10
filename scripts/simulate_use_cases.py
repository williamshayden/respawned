"""Run synthetic user journeys against PostgreSQL and save reviewable evidence.

Run from a development checkout with SIMULATION_POSTGRES_URL set. Each journey
creates and removes only its own randomly named schema. Draft generation and
review decisions are scripted; no provider or delivery service is called.
"""

from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from io import StringIO
import json
import os
from pathlib import Path
import time
import traceback
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from rich.console import Console
from sqlalchemy import create_engine, text

from respawned.api import ui
from respawned.api.app import app, get_connection, get_workflow_clock, get_workflow_policy
from respawned.client import APIError, RespawnedClient
from respawned.config import DEFAULT_POLICY_PATH
from respawned.core.candidates import Candidate
from respawned.cli.review import run_review
from respawned.core.policy import load_policy
from respawned.db.helpers.pg_connect import create_tables
from respawned.llm.adapter import LiteLLMAdapter


NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
POLICY = load_policy(DEFAULT_POLICY_PATH)


def opportunity(identity, *, contact=None, **changes):
    contact = contact or identity
    return {
        "id": identity,
        "contact_key": f"simulation:{contact}",
        "contact_name": contact.title(),
        "contact_email": f"{contact}@example.com",
        "owner_name": "Morgan",
        "status": "open",
        "created_at": (NOW - timedelta(days=5)).isoformat(),
        "preferred_channel": "email",
    } | changes


def activity(identity, kind="contact_replied", *, hours=1, suffix=None):
    return {
        "id": f"{identity}:{suffix or kind}",
        "opportunity_id": identity,
        "type": kind,
        "occurred_at": (NOW - timedelta(hours=hours)).isoformat(),
        "channel": "email",
        "direction": "outbound" if kind == "message_sent" else "inbound",
    }


@contextmanager
def simulation_authorization():
    """Own one temporary operator credential without changing caller configuration."""
    previous = os.environ.get("RESPAWNED_REVIEW_TOKEN")
    token = uuid4().hex
    os.environ["RESPAWNED_REVIEW_TOKEN"] = token
    try:
        yield {"Authorization": f"Bearer {token}"}
    finally:
        if previous is None:
            os.environ.pop("RESPAWNED_REVIEW_TOKEN", None)
        else:
            os.environ["RESPAWNED_REVIEW_TOKEN"] = previous


class InProcessWorkflowClient(RespawnedClient):
    """Exercise SDK routes and CLI review against TestClient, without TCP claims."""

    def __init__(self, http):
        super().__init__("http://127.0.0.1")
        self.http = http

    def _request(self, method, path, payload=None, *, scope="workflow", csv=False):
        response = self.http.request(method, path, **({"json": payload} if payload is not None else {}))
        if not response.is_success:
            raise APIError(str(response.json().get("detail", "Simulation request failed")),
                           response.status_code, method not in {"GET", "HEAD"} and response.status_code >= 500)
        return response.text if csv else response.json()


class Journey:
    def __init__(self, engine, client, directory):
        self.engine, self.client, self.directory = engine, client, directory
        self.api = InProcessWorkflowClient(client)
        self.now, self.adapter = NOW, None
        app.dependency_overrides[get_workflow_clock] = lambda: lambda: self.now
        app.dependency_overrides[get_workflow_policy] = lambda: POLICY
        app.dependency_overrides[ui.get_draft_adapter_factory] = lambda: lambda: self.adapter
        self.steps = []
        self.requests = []
        self.prompts = []
        self.reviews = []

    def check(self, description, condition):
        self.steps.append({"description": description, "passed": bool(condition)})
        if not condition:
            raise AssertionError(description)

    def ingest(self, opportunities=(), activities=(), *, status=200):
        payload = {"opportunities": list(opportunities), "activities": list(activities)}
        response = self.client.post("/v1/ingest", json=payload)
        self.requests.append({"request": payload, "status": response.status_code,
                              "response": response.json()})
        self.check(f"Ingestion returns HTTP {status}", response.status_code == status)
        return response.json()

    def rows(self, table):
        if table not in {"opportunities", "activities", "candidates", "drafts", "outbox"}:
            raise ValueError("Unsupported evidence table")
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(
                text(f"SELECT * FROM {table} ORDER BY id")
            ).mappings()]

    def sync(self, *, now=NOW):
        self.now = now
        self.api.sync()
        candidates = TypeAdapter(list[Candidate]).validate_python(self.api.queue()["items"])
        self.steps.append({"description": "Refresh follow-up queue through HTTP", "passed": True,
                           "candidates": [asdict(item) for item in candidates]})
        return candidates

    def review(self, actions, *, body="Hello, thanks for getting in touch. How can Morgan help?",
               edit=None, before_action=None, now=NOW):
        actions = iter(actions)
        screen = StringIO()
        console = Console(file=screen, width=110, force_terminal=False, no_color=True)

        def complete(**request):
            self.prompts.append(request["messages"])
            if isinstance(body, Exception):
                raise body
            return {"choices": [{"message": {"content": body}}]}

        def decide(*_args, **_kwargs):
            # This is an explicitly simulated reviewer, never human authorization.
            self.check("Review displays the destination and copy before asking for action",
                       "@example.com" in screen.getvalue() and "Draft message" in screen.getvalue())
            action = next(actions)
            if before_action is not None:
                before_action(action)
            console.print(f"Simulation reviewer chose: {action}")
            return action

        try:
            self.now = now
            self.adapter = LiteLLMAdapter(
                proxy_url="http://unused.invalid", master_key="simulation-only",
                model_alias="scripted", completion_fn=complete,
            )
            summary = run_review(self.api, console=console, action_prompt=decide,
                                 message_prompt=lambda *_: edit)
            self.check("All scripted review actions were consumed", next(actions, None) is None)
            self.steps.append({"description": "Review result", "passed": True, **asdict(summary)})
            return summary
        finally:
            self.reviews.append(screen.getvalue())

    def save(self):
        evidence = {"http": self.requests, "model_prompts": self.prompts,
                    "tables": {name: self.rows(name) for name in
                               ("opportunities", "activities", "candidates", "drafts", "outbox")}}
        (self.directory / "evidence.json").write_text(
            json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
        (self.directory / "review.txt").write_text(
            "\n\n".join(self.reviews), encoding="utf-8")


def customer_reply(j):
    records = [opportunity("website", contact="avery"), opportunity("support", contact="avery")]
    events = [activity("website", "message_sent", hours=96), activity("website")]
    j.ingest(records, events)
    replay = j.ingest(records, events)
    j.check("Repeating the source batch adds no duplicate activities", replay["activities_inserted"] == 0)
    queue = j.sync()
    j.check("Two opportunities remain distinct but produce one response for Avery",
            len(queue) == 1 and len(j.rows("opportunities")) == 2 and
            set((queue[0].primary_opportunity_id, *queue[0].other_opportunity_ids)) == {"website", "support"})
    j.check("The queue correctly says we owe the customer a response", queue[0].reason == "replied_no_answer")
    j.review(["s"])
    drafts = j.rows("drafts")
    j.check("Skip keeps a pending draft and reserves nothing",
            len(drafts) == 1 and drafts[0]["status"] == "pending" and not j.rows("outbox"))
    edited = "Hi Avery, Morgan here. Happy to discuss both projects. Is Thursday suitable?\n\nFollow-up Team"
    j.review(["e", "a"], edit=edited)
    outbox = j.rows("outbox")
    j.check("Reopening and editing the draft makes no second model call", len(j.prompts) == 1)
    j.check("Only the exact edited copy is reserved, still unsent",
            len(outbox) == 1 and outbox[0]["body"] == edited and outbox[0]["status"] == "pending")
    (j.directory / "outbox.csv").write_text(j.api.export_outbox(format="csv"), encoding="utf-8", newline="")
    with (j.directory / "outbox.csv").open(encoding="utf-8", newline="") as handle:
        exported = list(csv.DictReader(handle))
    j.check("CSV preserves the reviewed recipient and copy",
            len(exported) == 1 and exported[0]["body"] == edited and
            exported[0]["contact_address"] == "avery@example.com")
    j.check("The approved contact leaves the follow-up queue", not j.sync())
    j.ingest(activities=[activity("support", hours=0, suffix="new-reply-after-reservation")])
    j.check("New evidence cannot bypass the contact-wide outbox cooldown",
            not j.sync(now=NOW + timedelta(minutes=1)))


def repeated_views(j):
    records = [opportunity("studio", contact="devon"), opportunity("licensing", contact="devon"),
               opportunity("separate-client", contact="erin", contact_email="devon@example.com")]
    events = [activity(identity, "content_viewed", hours=hours, suffix=f"view-{hours}")
              for identity in ("studio", "separate-client") for hours in (30, 54)]
    j.ingest(records, events)
    queue = j.sync()
    j.check("Views on different dates trigger repeat-view follow-up",
            len(queue) == 2 and all(item.reason == "repeat_views" for item in queue))
    j.check("Distinct contact identities remain separate even with a shared mailbox",
            {item.contact_key for item in queue} == {"simulation:devon", "simulation:erin"})
    j.review(["r", "r"])
    j.check("Rejecting both suggestions creates no delivery reservation", not j.rows("outbox"))
    j.check("The same rejected evidence does not reappear", not j.sync())
    j.ingest(activities=[activity("studio", hours=0)])
    changed = j.sync(now=NOW + timedelta(minutes=1))
    j.check("A new customer reply resurfaces just that contact with a new reason",
            len(changed) == 1 and changed[0].contact_key == "simulation:devon" and
            changed[0].reason == "replied_no_answer")


def ingestion_conflict(j):
    record, event = opportunity("casey"), activity("casey")
    j.ingest([record], [event])
    j.ingest([record | {"contact_email": "changed@example.com"}],
             [event | {"type": "content_viewed"}], status=409)
    j.check("A conflicting event rolls back the accompanying recipient change",
            j.rows("opportunities")[0]["contact_email"] == "casey@example.com")
    j.ingest([opportunity("new-record")], [activity("missing-record")], status=409)
    j.check("An orphan event rolls back the entire source batch",
            len(j.rows("opportunities")) == 1 and len(j.rows("activities")) == 1)
    j.check("The original valid opportunity remains usable", len(j.sync()) == 1)


def closed_during_review(j):
    record = opportunity("frankie")
    j.ingest([record], [activity("frankie")])
    j.sync()

    def close(_action):
        j.ingest([record | {"status": "lost"}])

    summary = j.review(["a"], before_action=close)
    j.check("Closure after display blocks approval against current ingested state",
            summary.blocked == 1 and summary.approved == 0 and not j.rows("outbox"))
    j.check("The closed opportunity leaves the queue", not j.sync())


def route_changed_during_review(j):
    record = opportunity("gray")
    j.ingest([record], [activity("gray")])
    j.sync()

    def reroute(_action):
        j.ingest([record | {"contact_email": "new-gray@example.com"}])

    summary = j.review(["a"], before_action=reroute)
    j.check("A recipient change blocks the already displayed draft",
            summary.blocked == 1 and not j.rows("outbox"))
    refreshed = j.sync(now=NOW + timedelta(minutes=1))
    j.check("Refresh shows the updated destination",
            len(refreshed) == 1 and refreshed[0].contact_address == "new-gray@example.com")
    summary = j.review(["s"], now=NOW + timedelta(minutes=1))
    j.check("A new draft is reviewable at the corrected destination", summary.skipped == 1)


def failed_model(j):
    j.ingest([opportunity("harper")], [activity("harper")])
    j.sync()
    for copy in (TimeoutError("simulated provider timeout"), "Hello [NAME]", "The fee is USD 50."):
        result = j.review([], body=copy)
        j.check("Provider failure or invalid copy persists no draft or reservation",
                result.blocked == 1 and not j.rows("drafts") and not j.rows("outbox"))
    result = j.review(["s"])
    j.check("A valid retry recovers without losing the candidate",
            result.skipped == 1 and len(j.rows("drafts")) == 1 and not j.rows("outbox"))


def cooldown_boundary(j):
    records = [opportunity("original", contact="indigo"), opportunity("second", contact="indigo"),
               opportunity("closed", status="won"), opportunity("old", created_at=(NOW - timedelta(days=50)).isoformat())]
    events = [activity("original", "message_sent", hours=1), activity("second", hours=0),
              activity("closed"), activity("old")]
    j.ingest(records, events)
    j.check("Recent outbound suppresses even a fresh reply on another opportunity for that contact",
            not j.sync())
    inbox = j.client.get("/v1/inbox", params={"now": NOW.isoformat()})
    j.check("The reply-needed inbox exposes that reply independently of outreach cooldown",
            inbox.status_code == 200 and len(inbox.json()["items"]) == 1 and
            inbox.json()["items"][0]["opportunity_ids"] == ["second"])
    queue = j.sync(now=NOW + timedelta(hours=71))
    j.check("At exactly 72 hours the unresolved reply becomes available; closed and old records stay out",
            len(queue) == 1 and queue[0].contact_key == "simulation:indigo" and
            queue[0].reason == "replied_no_answer")


def tracking_boundary(j):
    receipt_only = {"id": "acme-backend", "kind": "job_application",
                    "title": "Backend engineer at Acme", "status": "open",
                    "created_at": NOW.isoformat(),
                    "context": {"company": "Acme", "role": "Backend engineer", "stage": "Applied"}}
    j.ingest([receipt_only])
    j.now = NOW + timedelta(days=8)
    contactless = j.api.sync(dry_run=True)
    j.check("Contactless applications remain tracked without inventing a delivery route",
            len(j.rows("opportunities")) == 1 and contactless["candidate_count"] == 0)
    j.ingest([receipt_only | {"contact_key": "simulation:known-recruiter"}], status=422)
    j.check("A partial contact identity does not overwrite the valid tracked application",
            j.rows("opportunities")[0]["contact_key"] is None)
    # A known recruiter is supplied here; a no-reply receipt is never used as a route.
    records = [opportunity(identity, contact="jules", kind="job_application", created_at=NOW.isoformat(),
                           title=f"{role} at Acme", context={"company": "Acme", "role": role, "stage": "Applied"})
               for identity, role in (("acme-backend", "Backend engineer"), ("acme-platform", "Platform engineer"))]
    events = [activity(record["id"], "automated_receipt") | {"classification": "automated"}
              for record in records]
    j.ingest(records, events)
    j.check("Automated receipt activities are retained without becoming human replies",
            len(j.rows("activities")) == 2 and not j.sync())
    queue = j.sync(now=NOW + timedelta(days=7))
    j.check("After seven days application-specific waiting groups both roles into one recruiter suggestion",
            len(queue) == 1 and queue[0].reason == "application_no_update" and
            len(j.rows("opportunities")) == 2 and len(queue[0].other_opportunity_ids) == 1)
    j.review(["s"], now=NOW + timedelta(days=7),
             body="Hi Jules, I wanted to check whether there are any updates. Thanks for your time.")
    prompt = json.dumps(j.prompts)
    primary_role = next(record["context"]["role"] for record in records
                        if record["id"] == queue[0].primary_opportunity_id)
    j.check("Drafting receives the actual application context and leaves the copy unsent",
            "Acme" in prompt and primary_role in prompt and "Applied" in prompt and not j.rows("outbox"))


SCENARIOS = (
    ("customer-reply", "A customer replies about two projects", customer_reply,
     "Simulated workflow passed", "One editable reply, exact unsent reservation, replay-safe import and CSV export."),
    ("repeated-views", "A client returns to a proposal", repeated_views,
     "Simulated workflow passed", "Contact grouping, distinct identities, rejection, and a later new reply work."),
    ("ingestion-conflict", "A connector retries a conflicting batch", ingestion_conflict,
     "Simulated workflow passed", "Conflicting and orphan events reject the entire request without partial changes."),
    ("closed-during-review", "An opportunity closes while its draft is displayed", closed_during_review,
     "Simulated workflow passed", "Approval rechecks ingested state and blocks the stale action."),
    ("recipient-change", "A contact address changes while reviewing", route_changed_during_review,
     "Simulated workflow passed", "Old approval is blocked; refresh provides the corrected destination."),
    ("model-failure", "The model times out or produces unusable copy", failed_model,
     "Simulated workflow passed", "Nothing unsafe persists; a later valid attempt recovers."),
    ("cooldown", "A customer replies immediately after outreach", cooldown_boundary,
     "Reply visibility improved", "The inbox shows the fresh reply immediately; the outbound follow-up queue retains its 72-hour cooldown."),
    ("application-tracking", "Two job applications produce automated receipts", tracking_boundary,
     "Simulated workflow passed", "Contactless applications are tracked; a known recruiter enables grouped application waiting and contextual drafting without sending."),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory for synthetic evidence")
    args = parser.parse_args()
    url = os.environ.get("SIMULATION_POSTGRES_URL")
    if not url:
        parser.error("Set SIMULATION_POSTGRES_URL to a disposable/test PostgreSQL database; CREATE SCHEMA permission is required")
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    started = time.monotonic()
    for slug, title, simulate, classification, conclusion in SCENARIOS:
        directory = args.output / slug
        directory.mkdir()
        schema = f"respawned_sim_{uuid4().hex}"
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"},
                               pool_size=2, max_overflow=0)
        created = False
        prior_overrides = app.dependency_overrides.copy()
        result = {"scenario": title, "slug": slug, "classification": classification,
                  "conclusion": conclusion, "passed": False}
        journey = None
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            created = True
            create_tables(engine)

            def connection_dependency():
                with engine.begin() as connection:
                    yield connection

            app.dependency_overrides[get_connection] = connection_dependency
            with simulation_authorization() as headers, TestClient(app, headers=headers) as client:
                journey = Journey(engine, client, directory)
                simulate(journey)
                result["passed"] = True
        except Exception:
            result["error"] = traceback.format_exc()
        finally:
            try:
                if journey is not None:
                    result["steps"] = journey.steps
                    journey.save()
            finally:
                app.dependency_overrides.clear()
                app.dependency_overrides.update(prior_overrides)
                try:
                    if created:
                        with engine.begin() as connection:
                            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
                finally:
                    engine.dispose()
        results.append(result)
        print(f"{'PASS' if result['passed'] else 'FAIL'} {title} ({classification})", flush=True)

    report = {"anchor_time": NOW.isoformat(), "policy": str(DEFAULT_POLICY_PATH),
              "duration_seconds": round(time.monotonic() - started, 3),
              "execution": "Real PostgreSQL commits; authenticated in-process HTTP workflow requests; API-backed CLI review with scripted actions; deterministic model responses; no delivery",
              "scenarios": results}
    (args.output / "results.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    lines = ["# Respawned scenario simulation", "", report["execution"] + ".", "",
             "These scripted journeys test the stated behavior; their verification limits are listed below.", "",
             "| Scenario | Execution | Assessment |", "| --- | --- | --- |"]
    for item in results:
        lines.append(f"| [{item['scenario']}]({item['slug']}/evidence.json) | {'PASS' if item['passed'] else 'FAIL'} | {item['classification'] if item['passed'] else 'Investigation required'} |")
    for item in results:
        lines.extend(["", f"## {item['scenario']}", "", item["conclusion"] if item["passed"] else "The scenario did not meet its assertions; inspect the error in results.json.", "",
                      f"[Review transcript]({item['slug']}/review.txt) · [Source requests and stored state]({item['slug']}/evidence.json)", ""])
        lines.extend(f"- {'PASS' if step['passed'] else 'FAIL'}: {step['description']}" for step in item.get("steps", []))
    lines.extend(["", "## Verification limits", "",
                  "Synthetic classifications and reviewer actions are supplied by this harness. Source discovery, semantic matching, actual human authorization, model quality, live HTTP transport, concurrent reviewers, delivery, Docker installation/persistence, restore, and transaction-commit failure responses are not exercised by this simulation. See docs/RELEASE_CHECKS.md for the separate release verification procedure and docs/V1_RELEASE.md for recorded qualification results.", ""])
    (args.output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Evidence: {args.output / 'REPORT.md'}")
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
