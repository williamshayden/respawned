"""The packaged Codex backend shares the simulation boundary and saved settings."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from respawned.api.setup import model_status
from respawned.core.settings import ModelSettings, configured_adapter, save_model_settings
from respawned.llm import codex
from respawned.llm.adapter import LLMAdapterError


@pytest.fixture
def cli(monkeypatch, tmp_path):
    binary = tmp_path / 'codex'
    binary.touch()
    monkeypatch.setenv('RESPAWNED_CODEX_BIN', str(binary))
    monkeypatch.setenv('RESPAWNED_CODEX_SCRATCH_DIR', str(tmp_path))
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-reach-child')
    monkeypatch.setenv('DB_PASSWORD', 'must-not-reach-child')
    monkeypatch.setenv('RESPAWNED_REVIEW_TOKEN', 'must-not-reach-child')
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        assert not any(key in kwargs['env'] for key in ('OPENAI_API_KEY', 'DB_PASSWORD', 'RESPAWNED_REVIEW_TOKEN'))
        if command[1:] == ['login', 'status']:
            return SimpleNamespace(returncode=0, stdout='', stderr='Logged in using ChatGPT')
        if command[1:] == ['--version']:
            return SimpleNamespace(returncode=0, stdout='codex-cli test', stderr='')
        output = Path(command[command.index('--output-last-message') + 1])
        schema = json.loads(Path(command[command.index('--output-schema') + 1]).read_text())
        assert schema['additionalProperties'] is False
        output.write_text('{"body":"Hi Avery, when would be a good time to talk?"}')
        return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed"}', stderr='')

    monkeypatch.setattr(codex.subprocess, 'run', run)
    return commands


def test_saved_codex_settings_use_same_packaged_backend_without_api_key(postgres_connection, cli):
    settings = ModelSettings(backend='codex_cli', model_alias='operator-selected-model', timeout_seconds=17)
    save_model_settings(postgres_connection, settings)
    status = model_status(settings)
    assert status.ready and status.login_ready and not status.key_configured and not status.verified
    assert not any('exec' in command for command, _ in cli), 'Saving/status must not make an inference call'
    adapter = configured_adapter(postgres_connection)
    assert isinstance(adapter, codex.CodexDraftingAdapter)
    assert adapter.complete([{'role': 'system', 'content': 'Use supplied facts.'}, {'role': 'user', 'content': 'Draft a follow-up.'}]).startswith('Hi Avery')
    command, kwargs = cli[-1]
    assert command[command.index('--model') + 1] == 'operator-selected-model'
    assert kwargs['timeout'] == 17
    assert 'SYSTEM:\nUse supplied facts.' in kwargs['input']
    for flag in ('--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '--output-schema'):
        assert flag in command
    for feature in ('plugins', 'apps', 'hooks', 'memories', 'shell_tool'):
        assert ['--disable', feature] in [command[i:i + 2] for i in range(len(command) - 1)]


@pytest.mark.parametrize('stream', ['null', '{"item":null}', '{invalid', '{"type":"turn.failed"}'])
def test_malformed_or_failed_cli_output_is_an_adapter_error(cli, monkeypatch, stream):
    adapter = codex.CodexDraftingAdapter()
    monkeypatch.setattr(codex.subprocess, 'run', lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=stream, stderr=''))
    with pytest.raises(LLMAdapterError):
        adapter.complete([{'role': 'user', 'content': 'Draft'}])


def test_missing_or_wrong_login_is_configuration_error(cli, monkeypatch):
    monkeypatch.setattr(codex.subprocess, 'run', lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout='Logged in using an API key', stderr=''))
    status = model_status(ModelSettings(backend='codex_cli'))
    assert not status.ready and not status.login_ready
    assert 'ChatGPT' in status.error
    with pytest.raises(ValueError, match='ChatGPT'):
        codex.CodexDraftingAdapter()
