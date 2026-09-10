"""The command router forwards connection options and rejects local workflow overrides."""

import importlib
from importlib.metadata import version
import json
import os
import runpy
import sys
from pathlib import Path

import pytest

from respawned import __main__ as entrypoint


def test_version_reports_installed_distribution(capsys):
    with pytest.raises(SystemExit) as failure:
        entrypoint.main(["--version"])
    assert failure.value.code == 0
    assert capsys.readouterr().out.strip() == f"respawned {version('respawned')}"


def test_invalid_server_port_does_not_break_unrelated_commands(monkeypatch, capsys):
    monkeypatch.setenv("APP_PORT", "invalid-port")
    with pytest.raises(SystemExit) as failure:
        entrypoint.main(["--version"])
    assert failure.value.code == 0
    assert "respawned" in capsys.readouterr().out
    with pytest.raises(SystemExit) as failure:
        entrypoint.main(["serve"])
    assert failure.value.code == 2
    assert "invalid int value" in capsys.readouterr().err


def test_module_entrypoint_preserves_handler_exit_status(monkeypatch):
    sync = importlib.import_module("respawned.cli.sync")
    monkeypatch.setattr(sync, "main", lambda _argv: 23)
    monkeypatch.setattr(sys, "argv", ["respawned", "sync"])
    with pytest.raises(SystemExit) as failure:
        runpy.run_path(entrypoint.__file__, run_name="__main__")
    assert failure.value.code == 23


def test_no_command_and_help_list_all_workflow_and_lifecycle_commands(capsys):
    assert entrypoint.main([]) == 0
    assert capsys.readouterr().out.startswith("usage: respawned ")
    with pytest.raises(SystemExit) as failure:
        entrypoint.main(["--help"])
    assert failure.value.code == 0
    output = capsys.readouterr().out
    for command in ("init", "serve", "ui", "import", "demo", "sync", "draft", "review", "inbox", "outbox", "process"):
        assert command in output


def test_demo_import_uses_explicit_seed_and_http_client(monkeypatch, tmp_path, capsys):
    demo = importlib.import_module("respawned.cli.demo")
    client, calls = object(), []
    monkeypatch.setattr(entrypoint, "client_from_args", lambda _args: client)
    monkeypatch.setattr(demo, "load_demo", lambda directory, connection: (
        calls.append((directory, connection)) or {"opportunities_upserted": 30, "activities_inserted": 82}
    ))
    assert entrypoint.main(["demo", "--seed-dir", str(tmp_path)]) == 0
    assert calls == [(tmp_path, client)]
    assert json.loads(capsys.readouterr().out) == {"opportunities_upserted": 30, "activities_inserted": 82}


def test_outbox_preserves_cross_platform_csv_destination(tmp_path):
    destination = tmp_path / "pending messages.csv"
    received = []
    entrypoint.main(["outbox", "--path", str(destination)], handlers={"outbox": lambda args: received.append(args.path)})
    assert received == [Path(destination)]


@pytest.mark.parametrize("arguments, url, timeout", [
    (["--api-url", "https://first.example", "--timeout", "7", "sync"], "https://first.example", 7),
    (["sync", "--api-url", "https://second.example/app", "--timeout", "9"], "https://second.example/app", 9),
    (["--api-url", "https://first.example", "sync", "--api-url", "https://second.example"], "https://second.example", 180),
])
def test_connection_options_work_before_or_after_the_command(arguments, url, timeout):
    received = []
    entrypoint.main(arguments, handlers={"sync": lambda args: received.append((args.api_url, args.timeout))})
    assert received == [(url, timeout)]


@pytest.mark.parametrize("command, arguments, forwarded", [
    ("import", ["--file", "-"], ["--file", "-"]),
    ("draft", ["source/id", "--body-file", "-"], ["source/id", "--body-file", "-"]),
    ("sync", ["--limit", "4", "--dry-run"], ["--limit", "4", "--dry-run"]),
    ("review", ["--limit", "4"], ["--limit", "4"]),
    ("inbox", ["--limit", "3", "--json"], ["--limit", "3", "--json"]),
    ("process", ["--limit", "2"], ["--limit", "2"]),
    ("outbox", ["--pending", "--json", "--limit", "3"], ["--json", "--pending", "--limit", "3"]),
])
def test_client_commands_forward_options_and_return_errors(monkeypatch, command, arguments, forwarded):
    module = importlib.import_module("respawned.cli." + ("ingest" if command == "import" else command))
    received = []
    monkeypatch.setattr(module, "main", lambda argv: received.append(argv) or 17)
    assert entrypoint.main(["--api-url", "https://engine.example", "--timeout", "8", command, *arguments]) == 17
    assert received == [["--timeout", "8.0", "--api-url", "https://engine.example", *forwarded]]


@pytest.mark.parametrize("command", ["sync", "review", "inbox"])
@pytest.mark.parametrize("flag, value", [("--now", "2026-09-09T12:00:00Z"), ("--policy", "local.yaml")])
def test_client_cannot_override_server_time_or_policy(command, flag, value, capsys):
    with pytest.raises(SystemExit) as failure:
        entrypoint.main([command, flag, value], handlers={command: lambda _args: pytest.fail("Override reached handler")})
    assert failure.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and "The engine owns time and policy" in output.err
    assert "RESPAWNED_POLICY_PATH" in output.err


@pytest.mark.parametrize("command", ["init", "serve", "ui"])
def test_remote_api_options_do_not_change_engine_lifecycle(command, capsys):
    with pytest.raises(SystemExit) as failure:
        entrypoint.main(["--api-url", "https://engine.example", command], handlers={command: lambda _args: pytest.fail("Unexpected lifecycle call")})
    assert failure.value.code == 2
    assert "engine lifecycle" in capsys.readouterr().err


@pytest.mark.parametrize("api_only", [False, True])
def test_serve_restores_asset_configuration_for_bearer_engine(monkeypatch, api_only):
    import uvicorn

    calls = []
    monkeypatch.setenv("RESPAWNED_REVIEW_TOKEN", "explicit-test-operator")
    monkeypatch.setenv("RESPAWNED_UI_DIST", "/existing/override")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs, os.environ.get("RESPAWNED_UI_DIST"))))
    assert entrypoint.main(["serve", "--port", "8123", *(["--api-only"] if api_only else [])]) == 0
    assert calls == [("respawned.api.app:app", {"host": "127.0.0.1", "port": 8123}, "off" if api_only else "/existing/override")]
    assert os.environ["RESPAWNED_UI_DIST"] == "/existing/override"


@pytest.mark.parametrize("module_entrypoint", [False, True])
@pytest.mark.parametrize("arguments, error", [
    ([], "one of the arguments --path --json is required"),
    (["--path", "outbox.csv", "--json"], "not allowed with argument"),
    (["--path", "outbox.csv", "--pending"], "--pending requires --json"),
    (["--path", "outbox.csv", "--limit", "1"], "--limit requires --pending --json"),
    (["--json", "--limit", "1"], "--limit requires --pending --json"),
    (["--pending", "--json", "--limit", "0"], "--limit must be between 1 and 200"),
    (["--pending", "--json", "--limit", "201"], "--limit must be between 1 and 200"),
    (["--pending", "--json", "--limit", "abc"], "invalid int value"),
])
def test_outbox_invalid_options_fail_before_http(monkeypatch, capsys, module_entrypoint, arguments, error):
    outbox = importlib.import_module("respawned.cli.outbox")
    monkeypatch.setattr(outbox, "client_from_args", lambda _args: pytest.fail("Invalid options created an HTTP client"))
    with pytest.raises(SystemExit) as failure:
        if module_entrypoint:
            outbox.main(arguments)
        else:
            entrypoint.main(["outbox", *arguments])
    assert failure.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and error in output.err
