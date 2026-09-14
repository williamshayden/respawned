"""Command cleanup uses only the subprocess's owned POSIX process group."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from respawned.llm import codex


def _running(pid):
    status = Path(f"/proc/{pid}/stat")
    if status.exists():
        # A terminated grandchild may briefly await its platform init's reaper.
        try:
            return status.read_text().split(")", 1)[1].split()[0] != "Z"
        except (FileNotFoundError, ProcessLookupError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups do not manage Windows descendants")
@pytest.mark.parametrize("outcome", ["timeout", "interrupt", "success"])
def test_owned_command_group_stops_descendants_and_reaps_wrapper(tmp_path, monkeypatch, outcome):
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        + ("print('completed')\n" if outcome == "success" else "time.sleep(30)\n")
    )
    processes = []
    original_popen = subprocess.Popen
    def start(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        assert kwargs["start_new_session"] is True
        if outcome == "interrupt":
            def interrupted(*_args, **_kwargs):
                deadline = time.monotonic() + 5
                while (not pid_file.exists() or not pid_file.read_text().isdigit()) and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert pid_file.exists() and pid_file.read_text().isdigit()
                raise KeyboardInterrupt
            process.communicate = interrupted
        return process
    monkeypatch.setattr(codex.subprocess, "Popen", start)
    child_pid = None
    try:
        arguments = ([sys.executable, "-c", code],)
        options = dict(input="synthetic prompt", env=codex.codex_environment(), timeout=0.5)
        if outcome == "timeout":
            with pytest.raises(subprocess.TimeoutExpired):
                codex._run_command(*arguments, **options)
        elif outcome == "interrupt":
            with pytest.raises(KeyboardInterrupt):
                codex._run_command(*arguments, **options)
        else:
            completed = codex._run_command(*arguments, **options)
            assert completed.returncode == 0
            assert completed.stdout == "completed\n"
        assert pid_file.exists()
        child_pid = int(pid_file.read_text())
        deadline = time.monotonic() + 2
        while _running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _running(child_pid), "Descendant continued after command completion"
        assert processes[0].returncode is not None
        with pytest.raises(ChildProcessError):
            os.waitpid(processes[0].pid, os.WNOHANG)
        assert os.getpgrp() != processes[0].pid, "Cleanup must not target the caller's group"
    finally:
        if child_pid is None and pid_file.exists() and pid_file.read_text().isdigit():
            child_pid = int(pid_file.read_text())
        if child_pid is not None and _running(child_pid):
            os.kill(child_pid, signal.SIGKILL)
