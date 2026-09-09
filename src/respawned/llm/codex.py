"""Bounded headless Codex using its existing ChatGPT login and structured output."""

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from pydantic import BaseModel, ConfigDict, Field

from respawned.llm.adapter import LLMAdapterError


class DraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)


def codex_environment() -> dict[str, str]:
    # Keep host runtime/login discovery, while excluding application credentials
    # and API billing overrides. Codex alone owns its login credential storage.
    allowed = {
        "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR",
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "PROGRAMDATA", "USER", "USERNAME", "LANG", "LC_ALL", "CODEX_HOME",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


class CodexRunner:
    """The same text-only execution boundary used by the agent simulations."""

    def __init__(self, binary="codex", scratch=None, *, timeout=120, model=None):
        self.binary = shutil.which(binary) or str(Path(binary).resolve())
        if not Path(self.binary).is_file():
            raise ValueError("Codex CLI was not found; set RESPAWNED_CODEX_BIN on the server")
        self.scratch = scratch
        self.timeout = float(timeout)
        if not 0 < self.timeout <= 300:
            raise ValueError("Codex timeout must be greater than 0 and at most 300 seconds")
        if scratch is not None and not Path(scratch).is_dir():
            raise ValueError("RESPAWNED_CODEX_SCRATCH_DIR must be an existing directory")
        self.model = model or None
        self.env = codex_environment()
        try:
            status = subprocess.run([self.binary, "login", "status"], env=self.env,
                                    capture_output=True, text=True, timeout=15)
            if status.returncode or "ChatGPT" not in status.stdout + status.stderr:
                raise ValueError("Run codex login on the server with your ChatGPT account")
            self.version = subprocess.run([self.binary, "--version"], env=self.env,
                                          capture_output=True, text=True, timeout=15,
                                          check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("Could not run Codex CLI; check the server executable and login") from exc
        self.calls = []

    def native_path(self, path):
        if os.name != "nt" and self.binary.lower().endswith(".exe"):
            if not str(path.resolve()).startswith("/mnt/"):
                raise ValueError("Windows Codex from WSL requires RESPAWNED_CODEX_SCRATCH_DIR on a mounted Windows drive")
            return subprocess.run(["wslpath", "-w", str(path.resolve())], capture_output=True,
                                  text=True, check=True, timeout=10).stdout.strip()
        return str(path.resolve())

    def ask(self, prompt, output_type, evidence=None):
        if evidence is not None:
            evidence = Path(evidence)
            evidence.parent.mkdir(parents=True, exist_ok=True)

        def save(suffix, value):
            if evidence is not None:
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                evidence.with_suffix(suffix).write_text(value or "", encoding="utf-8")

        with tempfile.TemporaryDirectory(prefix="respawned-agent-", dir=self.scratch) as folder:
            directory = Path(folder)
            schema, output = directory / "schema.json", directory / "answer.json"
            schema.write_text(json.dumps(output_type.model_json_schema()), encoding="utf-8")
            command = [self.binary, "-a", "never", "exec", "--ignore-user-config", "--ephemeral",
                       "--skip-git-repo-check", "--sandbox", "read-only", "--json", "--color", "never",
                       "-C", self.native_path(directory), "--output-schema", self.native_path(schema),
                       "--output-last-message", self.native_path(output), "-c", 'web_search="disabled"']
            for feature in ("plugins", "apps", "hooks", "memories", "shell_tool"):
                command.extend(["--disable", feature])
            if getattr(self, "model", None):
                command.extend(["--model", self.model])
            started = time.monotonic()
            try:
                completed = subprocess.run(command + ["-"], input=prompt, capture_output=True,
                                           text=True, encoding="utf-8", env=self.env, timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                save(".jsonl", exc.stdout)
                save(".stderr.txt", exc.stderr)
                raise RuntimeError(f"Codex exceeded {self.timeout}s; no draft was accepted") from exc
            save(".jsonl", completed.stdout)
            save(".stderr.txt", completed.stderr)
            if completed.returncode:
                raise RuntimeError(f"Codex exited {completed.returncode}; no draft was accepted")
            events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
            if any(not isinstance(event, dict) or not isinstance(event.get("item", {}), dict)
                   for event in events):
                raise RuntimeError("Codex returned an invalid event stream")
            prohibited = {"command_execution", "mcp_tool_call", "web_search", "file_change"}
            if any(event.get("item", {}).get("type") in prohibited for event in events):
                raise RuntimeError("Unexpected native tool use; drafting only accepts text output")
            if (not events or events[-1].get("type") != "turn.completed"
                    or any(event.get("type") == "turn.failed" for event in events)):
                raise RuntimeError("Codex did not complete a successful turn")
            answer = output_type.model_validate_json(output.read_text(encoding="utf-8-sig"))
            self.calls.append({"seconds": round(time.monotonic() - started, 3),
                               "usage": [event.get("usage") for event in events
                                         if event.get("type") == "turn.completed"]})
            return answer


class CodexDraftingAdapter:
    def __init__(self, *, model_alias="", timeout_seconds=120):
        self.runner = CodexRunner(
            os.environ.get("RESPAWNED_CODEX_BIN", "codex"),
            os.environ.get("RESPAWNED_CODEX_SCRATCH_DIR") or None,
            timeout=timeout_seconds, model=model_alias,
        )

    def complete(self, messages: Sequence[Mapping[str, str]]) -> str:
        prompt = "Write the requested follow-up as structured JSON with only a body field. Use no tools.\n"
        prompt += "\n\n".join(f"{message['role'].upper()}:\n{message['content']}" for message in messages)
        try:
            return self.runner.ask(prompt, DraftOutput).body
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            raise LLMAdapterError("Headless Codex drafting failed; check CLI login and server configuration") from exc
