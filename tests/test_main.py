import importlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_default_load_uses_repository_seed_despite_container_environment(
    monkeypatch,
    tmp_path,
):
    entrypoint = importlib.import_module("follow_up_engine.__main__")
    load_module = importlib.import_module("follow_up_engine.cli.load_data")
    loaded = {}

    def fake_load_data(quotes_source, events_source):
        loaded["quotes"] = quotes_source
        loaded["events"] = events_source

    monkeypatch.setattr(load_module, "load_data", fake_load_data)
    monkeypatch.setenv("SEED_DIR", "/seed")
    monkeypatch.setenv("SCHEMA_PATH", "/src/follow_up_engine/db/schema.sql")
    monkeypatch.setenv("QUOTES_FILENAME", "quotes.json")
    monkeypatch.setenv("EVENTS_FILENAME", "events.jsonl")
    monkeypatch.chdir(tmp_path)

    entrypoint.main([])

    assert loaded == {
        "quotes": {
            "type": "json",
            "path": str(ROOT / "seed" / "quotes.json"),
        },
        "events": {
            "type": "json",
            "path": str(ROOT / "seed" / "events.jsonl"),
        },
    }


def test_load_seed_dir_option_uses_explicit_path(monkeypatch, tmp_path):
    entrypoint = importlib.import_module("follow_up_engine.__main__")
    load_module = importlib.import_module("follow_up_engine.cli.load_data")
    loaded = {}

    def fake_load_data(quotes_source, events_source):
        loaded["quotes"] = quotes_source
        loaded["events"] = events_source

    monkeypatch.setattr(load_module, "load_data", fake_load_data)

    entrypoint.main(["load", "--seed-dir", str(tmp_path)])

    assert loaded == {
        "quotes": {
            "type": "json",
            "path": str(tmp_path / "quotes.json"),
        },
        "events": {
            "type": "json",
            "path": str(tmp_path / "events.jsonl"),
        },
    }


def test_help_lists_available_commands(capsys):
    entrypoint = importlib.import_module("follow_up_engine.__main__")

    with pytest.raises(SystemExit) as exc_info:
        entrypoint.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "load" in output
    assert "outbox" in output
    assert "sync" in output


def test_load_command_routes_to_injected_handler():
    entrypoint = importlib.import_module("follow_up_engine.__main__")
    calls = []

    entrypoint.main(
        ["load"],
        handlers={
            "load": lambda args: calls.append(("load", args.command)),
            "outbox": lambda _args: calls.append(("outbox", None)),
        },
    )

    assert calls == [("load", "load")]


def test_outbox_command_passes_cross_platform_path_to_injected_handler(tmp_path):
    entrypoint = importlib.import_module("follow_up_engine.__main__")
    calls = []
    destination = tmp_path / "pending messages.csv"

    entrypoint.main(
        ["outbox", "--path", str(destination)],
        handlers={
            "load": lambda _args: calls.append(("load", None)),
            "outbox": lambda args: calls.append(("outbox", args.path)),
        },
    )

    assert calls == [("outbox", Path(destination))]


def test_sync_command_delegates_arguments(monkeypatch, tmp_path):
    entrypoint = importlib.import_module("follow_up_engine.__main__")
    sync_module = importlib.import_module("follow_up_engine.cli.sync")
    policy_path = tmp_path / "policy.yaml"
    delegated = []

    def fake_sync_main(argv):
        delegated.append(argv)
        return 17

    monkeypatch.setattr(sync_module, "main", fake_sync_main)

    result = entrypoint.main(
        [
            "sync",
            "--dry-run",
            "--limit",
            "4",
            "--now",
            "2026-08-20T12:00:00Z",
            "--policy",
            str(policy_path),
        ]
    )

    assert result == 17
    assert delegated == [
        [
            "--dry-run",
            "--limit",
            "4",
            "--now",
            "2026-08-20T12:00:00Z",
            "--policy",
            str(policy_path),
        ]
    ]
