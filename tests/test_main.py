import importlib
from pathlib import Path

import pytest


def test_main_loads_seed_files_from_environment(monkeypatch):
    entrypoint = importlib.import_module("follow_up_engine.__main__")
    load_module = importlib.import_module("follow_up_engine.cli.load_data")
    loaded = {}

    def fake_load_data(quotes_source, events_source):
        loaded["quotes"] = quotes_source
        loaded["events"] = events_source

    monkeypatch.setattr(load_module, "load_data", fake_load_data)
    monkeypatch.setenv("SEED_DIR", "/seed")
    monkeypatch.setenv("QUOTES_FILENAME", "quotes.json")
    monkeypatch.setenv("EVENTS_FILENAME", "events.jsonl")

    entrypoint.main([])

    assert loaded == {
        "quotes": {"type": "json", "path": "/seed/quotes.json"},
        "events": {"type": "json", "path": "/seed/events.jsonl"},
    }


def test_help_lists_available_commands(capsys):
    entrypoint = importlib.import_module("follow_up_engine.__main__")

    with pytest.raises(SystemExit) as exc_info:
        entrypoint.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "load" in output
    assert "outbox" in output


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
