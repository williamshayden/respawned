from pathlib import Path

import yaml


CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "respawned"
    / "llm"
    / "litellm_proxy_config.yaml"
)


def test_proxy_alias_routes_through_environment_configured_upstream():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    assert config["model_list"] == [
        {
            "model_name": "respawned-default",
            "litellm_params": {
                "model": "os.environ/LITELLM_UPSTREAM_MODEL",
                "api_key": "os.environ/LITELLM_UPSTREAM_API_KEY",
            },
        }
    ]


def test_proxy_config_contains_no_provider_specific_routing():
    raw_config = CONFIG_PATH.read_text(encoding="utf-8").lower()

    for provider in ("anthropic/", "openrouter/", "openai/"):
        assert provider not in raw_config
    assert "api_base" not in raw_config
