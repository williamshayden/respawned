"""Serve the real UI/API on mock data with live Codex drafting.

Requires an explicit loopback test database and Codex executable. Owns one
temporary schema; shutdown removes only that schema. No sending provider runs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import shlex
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from fastapi import Depends
from sqlalchemy import text
import uvicorn

import serve_ui_simulation as harness
from respawned.api.app import get_workflow_adapter, get_workflow_clock
from respawned.api.ui import get_draft_adapter_factory, require_review_authorization
from respawned.core.settings import ModelSettings, save_model_settings
from respawned.core.workspaces import create_workspace
from respawned.llm.codex import CodexDraftingAdapter, CodexRunner

REVIEW_TOKEN = "respawned-live-demo-review"
OUTBOX_TOKEN = "respawned-live-demo-outbox"


class RecordedRunner(CodexRunner):
    def __init__(self, *args, evidence: Path, **kwargs):
        super().__init__(*args, **kwargs)
        self.evidence = evidence
        self.sequence = 0

    def ask(self, prompt, output_type, evidence=None):
        self.sequence += 1
        destination = self.evidence / f"draft-{self.sequence}"
        destination.with_suffix(".prompt.txt").write_text(prompt)
        try:
            answer = super().ask(prompt, output_type, evidence=destination)
            destination.with_suffix(".answer.json").write_text(answer.model_dump_json(indent=2))
            return answer
        finally:
            (self.evidence / "draft-calls.json").write_text(json.dumps(self.calls, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-url", required=True)
    parser.add_argument("--codex-bin", required=True)
    parser.add_argument("--scratch-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", default=8127, type=int)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be 1..65535")
    args.output.mkdir(parents=True, exist_ok=False)
    # These values belong only to the isolated demo process.
    os.environ.update(
        RESPAWNED_MODEL_BACKEND="codex_cli",
        RESPAWNED_CODEX_BIN=args.codex_bin,
        RESPAWNED_CODEX_SCRATCH_DIR=str(args.scratch_dir),
        RESPAWNED_OUTBOX_TOKEN=OUTBOX_TOKEN,
        RESPAWNED_UI_ORIGINS="",
    )
    harness.REVIEW_TOKEN = REVIEW_TOKEN
    with harness.simulation_api(args.postgres_url, empty=True) as (app, engine, schema):
        adapter = CodexDraftingAdapter(timeout_seconds=180)
        adapter.runner = RecordedRunner(
            args.codex_bin, str(args.scratch_dir), timeout=180, evidence=args.output,
        )
        app.dependency_overrides[get_workflow_adapter] = lambda: adapter
        app.dependency_overrides[get_draft_adapter_factory] = lambda: lambda: adapter
        app.dependency_overrides[get_workflow_clock] = lambda: lambda: datetime.now(UTC)

        with engine.begin() as connection:
            save_model_settings(connection, ModelSettings(backend="codex_cli", timeout_seconds=180))
            workspace = create_workspace(
                connection, name="Applications", kinds=["job_application"],
                description="Applications, interviews, and next steps.",
            )

        @app.post("/__demo/reset", dependencies=[Depends(require_review_authorization)])
        def reset():
            # Only the UUID schema owned by this context can be reset.
            if not schema.startswith("respawned_ui_simulation_") or not schema.replace("_", "").isalnum():
                raise RuntimeError("Unexpected demo schema")
            with engine.begin() as connection:
                tables = connection.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname=:schema "
                    "AND tablename NOT IN ('workspaces', 'application_settings')"
                ), {"schema": schema}).scalars().all()
                qualified = ", ".join(f'"{schema}"."{name}"' for name in tables)
                if qualified:
                    connection.exec_driver_sql(f"TRUNCATE {qualified} RESTART IDENTITY CASCADE")
            return {"reset": True}

        app.router.routes.insert(0, app.router.routes.pop())
        manifest = {
            "url": f"http://127.0.0.1:{args.port}", "schema": schema,
            "workspace_id": str(workspace["id"]),
            "review_token": REVIEW_TOKEN, "outbox_token": OUTBOX_TOKEN,
            "model": "live Codex", "data": "mock correspondence", "external_delivery": False,
            "record_id": "demo:application:northstar-backend",
        }
        (args.output / "runtime.json").write_text(json.dumps(manifest, indent=2))
        env = {
            "PYTHONPATH": str(REPO / "src"),
            "RESPAWNED_API_URL": manifest["url"], "RESPAWNED_REVIEW_TOKEN": REVIEW_TOKEN,
            "RESPAWNED_OUTBOX_TOKEN": OUTBOX_TOKEN,
        }
        (args.output / "cli-env.sh").write_text(
            "# Disposable demo environment only.\n" +
            "\n".join(f"export {key}={shlex.quote(value)}" for key, value in env.items()) + "\n"
        )
        print(json.dumps({"url": manifest["url"], "output": str(args.output),
                          "model": "live Codex", "schema": schema}), flush=True)
        try:
            uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        finally:
            app.router.routes[:] = [
                route for route in app.router.routes if getattr(route, "path", None) != "/__demo/reset"
            ]


if __name__ == "__main__":
    main()