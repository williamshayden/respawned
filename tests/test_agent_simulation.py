"""Verify the simulation oracle and failure boundary without any model calls."""

import importlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def simulation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("simulate_agents")


@pytest.mark.parametrize("shift_timestamp", [False, True])
def test_oracle_accepts_equal_instants_but_rejects_changed_source_time(
    simulation, postgres_engine, tmp_path, shift_timestamp
):
    case = simulation.cases()[0]
    payload = {"activities": [
        {"id": f"simulation-mail:{message['id']}",
         "opportunity_id": case["expected"][message["id"]][0],
         "type": case["expected"][message["id"]][1],
         "occurred_at": message["occurred_at"], "channel": "email",
         "direction": message["direction"]}
        for message in case["messages"]
    ]}
    if shift_timestamp:
        payload["activities"][1]["occurred_at"] = "2026-09-08T11:05:00Z"
    decisions = iter([
        ("read_messages", {}), ("ingest", payload), ("ingest", payload), ("inbox", {}),
        ("finish", {"summary": "Synthetic agent stub", "imported_source_ids": list(case["expected"]),
                    "unresolved": [], "reply_needed_ids": ["website"]}),
    ])

    def ask(*_args):
        tool, arguments = next(decisions)
        return simulation.Action(tool=tool, arguments_json=json.dumps(arguments))

    result = simulation.run_case(case, SimpleNamespace(ask=ask), postgres_engine.url,
                                 tmp_path / "case", max_turns=6)
    assert result["passed"] is not shift_timestamp, result
    if shift_timestamp:
        assert result["error"] == "AssertionError: Provider timestamps and directions are preserved"


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_codex_failure_rejects_even_a_valid_output_file(
    simulation, tmp_path, monkeypatch, failure
):
    # Avoid authentication entirely: this checks subprocess failure handling only.
    runner = simulation.Codex.__new__(simulation.Codex)
    runner.binary, runner.scratch, runner.timeout = "codex", tmp_path, 1
    runner.env, runner.calls = {}, []

    def execute(command, **kwargs):
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text('{"body":"A seemingly valid draft"}', encoding="utf-8")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1, output=b"partial event", stderr=b"partial error")
        return SimpleNamespace(returncode=1, stdout="", stderr="synthetic failure")

    monkeypatch.setattr(simulation.subprocess, "run", execute)
    evidence = tmp_path / "evidence" / "call"
    with pytest.raises(RuntimeError, match="exceeded|exited"):
        runner.ask("test", simulation.DraftOutput, evidence)
    assert runner.calls == []
    assert evidence.with_suffix(".stderr.txt").exists()
    assert not list(tmp_path.glob("respawned-agent-*"))


@pytest.mark.parametrize("events, accepted", [
    ([{"type": "error", "message": "Reconnecting"}, {"type": "turn.completed"}], True),
    ([{"type": "error", "message": "Disconnected"}], False),
    ([{"type": "turn.failed"}], False),
    ([{"type": "turn.failed"}, {"type": "turn.completed"}], False),
    ([{"type": "turn.completed"}, {"type": "error"}], False),
    ([{"type": "item.completed", "item": {"type": "command_execution"}},
      {"type": "turn.completed"}], False),
    ([], False),
])
def test_codex_requires_successful_completion_and_no_prohibited_tools(
    simulation, tmp_path, monkeypatch, events, accepted,
):
    runner = simulation.Codex.__new__(simulation.Codex)
    runner.binary, runner.scratch, runner.timeout = "codex", tmp_path, 1
    runner.env, runner.calls = {}, []

    def execute(command, **kwargs):
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text('{"body":"Validated output"}', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="\n".join(map(json.dumps, events)), stderr="")

    monkeypatch.setattr(simulation.subprocess, "run", execute)
    if accepted:
        assert runner.ask("test", simulation.DraftOutput, tmp_path / "call").body == "Validated output"
        assert len(runner.calls) == 1
    else:
        with pytest.raises(RuntimeError, match="successful turn|Unexpected native tool"):
            runner.ask("test", simulation.DraftOutput, tmp_path / "call")
        assert runner.calls == []
