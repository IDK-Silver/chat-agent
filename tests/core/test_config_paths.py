from pathlib import Path

import yaml

from chat_agent.core import config as config_module


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def test_resolve_llm_config_accepts_cfgs_prefix(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "openai" / "profile.yaml",
        {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test-key",
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.resolve_llm_config("cfgs/llm/openai/profile.yaml")
    assert config.model == "gpt-4o"


def test_load_config_accepts_cfgs_prefixed_llm_path(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "openai" / "profile.yaml",
        {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test-key",
        },
    )
    _write_yaml(
        tmp_path / "basic.yaml",
        {"agents": {"brain": {"llm": "cfgs/llm/openai/profile.yaml"}}},
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.load_config("basic.yaml")
    assert config.agents["brain"].llm.model == "gpt-4o"


def test_load_config_resolves_llm_fallback_paths(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "primary.yaml",
        {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "primary-key",
        },
    )
    _write_yaml(
        tmp_path / "llm" / "fallback.yaml",
        {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "api_key": "fallback-key",
            "site_url": "https://chat-agent.local",
        },
    )
    _write_yaml(
        tmp_path / "basic.yaml",
        {
            "agents": {
                "brain": {
                    "llm": "llm/primary.yaml",
                    "llm_fallbacks": ["cfgs/llm/fallback.yaml"],
                }
            }
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.load_config("basic.yaml")

    assert config.agents["brain"].llm.model == "gpt-4o"
    assert len(config.agents["brain"].llm_fallbacks) == 1
    assert config.agents["brain"].llm_fallbacks[0].provider == "openrouter"
    assert config.agents["brain"].llm_fallbacks[0].site_url == (
        "https://chat-agent.local/brain"
    )


def test_resolve_llm_config_reads_ollama_api_key_from_env(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "ollama" / "cloud.yaml",
        {
            "provider": "ollama",
            "model": "gpt-oss:20b-cloud",
            "base_url": "https://ollama.com",
            "api_key_env": "OLLAMA_API_KEY",
            "thinking": {"mode": "effort", "effort": "medium"},
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)
    monkeypatch.setattr(config_module, "_dotenv_values", {})
    monkeypatch.setenv("OLLAMA_API_KEY", "env-ollama-key")

    config = config_module.resolve_llm_config("llm/ollama/cloud.yaml")
    assert config.api_key == "env-ollama-key"


def test_repo_agent_config_enables_shell_handoff_rules():
    config = config_module.load_config("agent.yaml")

    handoff = config.tools.shell.handoff
    assert handoff.enabled is True
    assert [rule.id for rule in handoff.rules] == [
        "auth_browser_url",
        "auth_code_prompt",
        "press_enter_to_continue",
        "interactive_menu_prompt",
    ]


def test_repo_agent_config_brain_uses_ollama_kimi_k26_with_expected_fallbacks():
    config = config_module.load_config("agent.yaml")

    brain_llm = config.agents["brain"].llm
    assert brain_llm.provider == "ollama"
    assert brain_llm.model == "kimi-k2.6:cloud"

    fallbacks = config.agents["brain"].llm_fallbacks
    assert [cfg.provider for cfg in fallbacks] == ["codex", "ollama"]
    assert [cfg.model for cfg in fallbacks] == [
        "gpt-5.4",
        "qwen3.5:397b-cloud",
    ]


def test_repo_kimi_k26_cloud_profile_loads():
    config = config_module.resolve_llm_config(
        "cfgs/llm/ollama/kimi-k2.6-cloud/thinking.yaml"
    )

    assert config.provider == "ollama"
    assert config.model == "kimi-k2.6:cloud"
    assert config.vision is True
    assert config.thinking.mode == "toggle"
    assert config.thinking.enabled is True


def test_load_app_timezone_reads_only_timezone(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "agent.yaml",
        {
            "app": {"timezone": "Asia/Taipei"},
            "agents": {"brain": {"llm": "missing-llm.yaml"}},
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    assert config_module.load_app_timezone("agent.yaml") == "Asia/Taipei"
