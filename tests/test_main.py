import importlib
from importlib.metadata import version
import os
import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest


def test_version_reports_installed_distribution(capsys):
    entrypoint = importlib.import_module("respawned.__main__")
    with pytest.raises(SystemExit) as exc_info:
        entrypoint.main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == (
        f"respawned {version('respawned')}"
    )


def test_invalid_server_port_does_not_break_unrelated_commands(monkeypatch, capsys):
    entrypoint = importlib.import_module("respawned.__main__")
    monkeypatch.setenv("APP_PORT", "invalid-port")
    with pytest.raises(SystemExit) as version_exit:
        entrypoint.main(["--version"])
    assert version_exit.value.code == 0
    assert "respawned" in capsys.readouterr().out
    with pytest.raises(SystemExit) as serve_exit:
        entrypoint.main(["serve"])
    assert serve_exit.value.code == 2
    assert "invalid int value" in capsys.readouterr().err


def test_module_entrypoint_preserves_handler_exit_status(monkeypatch):
    entrypoint = importlib.import_module("respawned.__main__")
    sync_module = importlib.import_module("respawned.cli.sync")
    monkeypatch.setattr(sync_module, "main", lambda _argv: 23)
    monkeypatch.setattr(sys, "argv", ["respawned", "sync"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(entrypoint.__file__, run_name="__main__")
    assert exc_info.value.code == 23


def test_no_command_shows_help_without_loading_seed(capsys):
    entrypoint = importlib.import_module("respawned.__main__")
    assert entrypoint.main([]) == 0
    output = capsys.readouterr().out
    assert output.startswith("usage: respawned ")
    assert "demo" in output
    assert "serve" in output


def test_demo_seed_dir_option_uses_explicit_path(monkeypatch, tmp_path):
    entrypoint = importlib.import_module("respawned.__main__")
    demo_module = importlib.import_module("respawned.cli.demo")
    calls = []

    class Result:
        opportunities_upserted = 30
        activities_inserted = 82

    monkeypatch.setattr(
        demo_module, "load_demo", lambda seed_dir: calls.append(seed_dir) or Result()
    )

    entrypoint.main(["demo", "--seed-dir", str(tmp_path)])

    assert calls == [tmp_path]


def test_help_lists_available_commands(capsys):
    entrypoint = importlib.import_module("respawned.__main__")

    with pytest.raises(SystemExit) as exc_info:
        entrypoint.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "init" in output
    assert "demo" in output
    assert "serve" in output
    assert "outbox" in output
    assert "review" in output
    assert "sync" in output
    assert "inbox" in output


def test_init_command_routes_to_injected_handler():
    entrypoint = importlib.import_module("respawned.__main__")
    calls = []

    entrypoint.main(
        ["init"],
        handlers={
            "init": lambda args: calls.append(("init", args.command)),
            "outbox": lambda _args: calls.append(("outbox", None)),
        },
    )

    assert calls == [("init", "init")]


def test_outbox_command_passes_cross_platform_path_to_injected_handler(tmp_path):
    entrypoint = importlib.import_module("respawned.__main__")
    calls = []
    destination = tmp_path / "pending messages.csv"

    entrypoint.main(
        ["outbox", "--path", str(destination)],
        handlers={
            "outbox": lambda args: calls.append(("outbox", args.path)),
        },
    )

    assert calls == [("outbox", Path(destination))]


def test_sync_command_delegates_arguments(monkeypatch, tmp_path):
    entrypoint = importlib.import_module("respawned.__main__")
    sync_module = importlib.import_module("respawned.cli.sync")
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


def test_review_command_delegates_arguments(monkeypatch, tmp_path):
    entrypoint = importlib.import_module("respawned.__main__")
    review_module = ModuleType("respawned.cli.review")
    policy_path = tmp_path / "policy.yaml"
    delegated = []

    def fake_review_main(argv):
        delegated.append(argv)
        return 23

    review_module.main = fake_review_main
    monkeypatch.setitem(sys.modules, "respawned.cli.review", review_module)

    result = entrypoint.main(
        [
            "review",
            "--now",
            "2026-08-20T12:00:00Z",
            "--policy",
            str(policy_path),
        ]
    )

    assert result == 23
    assert delegated == [
        [
            "--now",
            "2026-08-20T12:00:00Z",
            "--policy",
            str(policy_path),
        ]
    ]


def test_inbox_command_delegates_arguments(monkeypatch, tmp_path):
    entrypoint = importlib.import_module("respawned.__main__")
    inbox_module = importlib.import_module("respawned.cli.inbox")
    delegated = []
    monkeypatch.setattr(inbox_module, "main", lambda argv: delegated.append(argv) or 0)
    policy_path = tmp_path / "policy.yaml"

    assert entrypoint.main([
        "inbox", "--json", "--limit", "3", "--now", "2026-09-08T12:00:00Z",
        "--policy", str(policy_path),
    ]) == 0
    assert delegated == [[
        "--now", "2026-09-08T12:00:00Z", "--limit", "3", "--json",
        "--policy", str(policy_path),
    ]]


@pytest.mark.parametrize("api_only", [False, True])
def test_serve_selects_bundled_ui_or_api_only(monkeypatch, api_only):
    entrypoint = importlib.import_module("respawned.__main__")
    import uvicorn

    calls = []
    monkeypatch.setenv("RESPAWNED_UI_DIST", "/existing/override")
    monkeypatch.setattr(entrypoint, "_init", lambda _args: pytest.fail("Serving the bundled demo must not access PostgreSQL"))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(
        (app, kwargs, os.environ.get("RESPAWNED_UI_DIST"))
    ))
    entrypoint.main(["serve", "--port", "8123", *(["--api-only"] if api_only else [])])

    assert calls == [(
        "respawned.api.app:app", {"host": "127.0.0.1", "port": 8123},
        "off" if api_only else "/existing/override",
    )]
    assert os.environ["RESPAWNED_UI_DIST"] == "/existing/override"
