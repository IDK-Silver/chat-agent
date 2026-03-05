"""Tests for per-provider reasoning validation via config.validate_reasoning()."""

from pathlib import Path

import pytest
import yaml

from chat_agent.core import config as config_module


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def test_resolve_llm_config_rejects_extra_fields(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "ollama",
            "model": "test-model",
            "unknown_key": "boom",
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        config_module.resolve_llm_config("llm/x.yaml")


def test_ollama_fails_when_reasoning_has_no_capabilities(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "ollama",
            "model": "test-model",
            "reasoning": {"enabled": True},
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(ValueError, match="capabilities.reasoning is missing"):
        config_module.resolve_llm_config("llm/x.yaml")


def test_ollama_fails_on_unsupported_effort(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "ollama",
            "model": "test-model",
            "reasoning": {"effort": "high"},
            "capabilities": {
                "reasoning": {
                    "supports_toggle": True,
                    "supported_efforts": ["low"],
                    "supports_max_tokens": False,
                }
            },
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(ValueError, match="is not supported"):
        config_module.resolve_llm_config("llm/x.yaml")


def test_openrouter_rejects_effort_and_max_tokens_together(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "openrouter",
            "model": "provider/model",
            "api_key": "test-key",
            "reasoning": {
                "effort": "high",
                "max_tokens": 2048,
                "supported_efforts": ["low", "medium", "high"],
            },
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(ValueError, match="mutually exclusive"):
        config_module.resolve_llm_config("llm/x.yaml")


def test_openrouter_provider_routing_rejects_empty_order(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "api_key": "test-key",
            "provider_routing": {"order": []},
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(ValueError, match="provider_routing.order must not be empty"):
        config_module.resolve_llm_config("llm/x.yaml")


def test_openrouter_provider_routing_null_is_allowed(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "api_key": "test-key",
            "provider_routing": None,
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.resolve_llm_config("llm/x.yaml")
    assert config.provider_routing is None


def test_openrouter_provider_routing_accepts_google_vertex(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.6",
            "api_key": "test-key",
            "provider_routing": {
                "order": ["google-vertex"],
                "allow_fallbacks": False,
            },
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.resolve_llm_config("llm/x.yaml")
    assert config.provider_routing is not None
    assert config.provider_routing.order == ["google-vertex"]
    assert config.provider_routing.allow_fallbacks is False


def test_openai_validates_reasoning(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "openai" / "profile.yaml",
        {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test-key",
            "reasoning": {"effort": "high"},
            "capabilities": {
                "reasoning": {
                    "supports_toggle": True,
                    "supported_efforts": ["low", "medium", "high"],
                    "supports_max_tokens": False,
                }
            },
        },
    )
    _write_yaml(
        tmp_path / "basic.yaml",
        {"agents": {"brain": {"llm": "llm/openai/profile.yaml"}}},
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.load_config("basic.yaml")
    assert config.agents["brain"].llm.reasoning.effort == "high"


def test_anthropic_requires_budget_tokens(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "anthropic",
            "model": "claude-test",
            "api_key": "test-key",
            "reasoning": {"enabled": True},
            "capabilities": {
                "reasoning": {
                    "supports_toggle": True,
                    "supported_efforts": [],
                    "supports_max_tokens": True,
                }
            },
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(ValueError, match="Anthropic thinking requires"):
        config_module.resolve_llm_config("llm/x.yaml")


def test_copilot_validates_supported_efforts(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "copilot",
            "model": "test-model",
            "reasoning": {"effort": "high", "supported_efforts": ["low"]},
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(ValueError, match="is not supported"):
        config_module.resolve_llm_config("llm/x.yaml")


def test_copilot_passes_with_valid_effort(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "copilot",
            "model": "test-model",
            "reasoning": {
                "effort": "high",
                "supported_efforts": ["low", "medium", "high"],
            },
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.resolve_llm_config("llm/x.yaml")
    assert config.reasoning.effort == "high"
    assert config.reasoning.enabled is True


def test_copilot_no_reasoning_passes(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {"provider": "copilot", "model": "test-model"},
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.resolve_llm_config("llm/x.yaml")
    assert config.reasoning is None


# --- OpenRouter global merge & site_name fallback ---


def test_load_config_site_name_fallback_to_agent_name(monkeypatch, tmp_path: Path):
    """site_name defaults to agent name when null in YAML."""
    _write_yaml(
        tmp_path / "llm" / "or.yaml",
        {"provider": "openrouter", "model": "test/model"},
    )
    _write_yaml(
        tmp_path / "basic.yaml",
        {"agents": {"brain": {"llm": "llm/or.yaml"}}},
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.load_config("basic.yaml")
    assert config.agents["brain"].llm.site_name == "brain"


def test_load_config_yaml_site_name_preserved(monkeypatch, tmp_path: Path):
    """site_name set in YAML is preserved (no override)."""
    _write_yaml(
        tmp_path / "llm" / "or.yaml",
        {"provider": "openrouter", "model": "test/model", "site_name": "from-yaml"},
    )
    _write_yaml(
        tmp_path / "basic.yaml",
        {"agents": {"brain": {"llm": "llm/or.yaml"}}},
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.load_config("basic.yaml")
    assert config.agents["brain"].llm.site_name == "from-yaml"


def test_load_config_app_openrouter_site_name_global(monkeypatch, tmp_path: Path):
    """app.openrouter_site_name overrides agent_name fallback for all agents."""
    _write_yaml(
        tmp_path / "llm" / "or.yaml",
        {"provider": "openrouter", "model": "test/model"},
    )
    _write_yaml(
        tmp_path / "basic.yaml",
        {
            "app": {"openrouter_site_name": "MyApp"},
            "agents": {"brain": {"llm": "llm/or.yaml"}},
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.load_config("basic.yaml")
    assert config.agents["brain"].llm.site_name == "MyApp"


def test_load_config_site_url_appends_agent_name(monkeypatch, tmp_path: Path):
    """site_url defaults to per-agent path when set in shared LLM config."""
    _write_yaml(
        tmp_path / "llm" / "or.yaml",
        {
            "provider": "openrouter",
            "model": "test/model",
            "site_url": "https://chat-agent.local",
        },
    )
    _write_yaml(
        tmp_path / "basic.yaml",
        {"agents": {"brain": {"llm": "llm/or.yaml"}}},
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.load_config("basic.yaml")
    assert config.agents["brain"].llm.site_url == "https://chat-agent.local/brain"


def test_load_config_site_url_preserves_existing_path(monkeypatch, tmp_path: Path):
    """site_url keeps existing path and appends agent segment."""
    _write_yaml(
        tmp_path / "llm" / "or.yaml",
        {
            "provider": "openrouter",
            "model": "test/model",
            "site_url": "https://chat-agent.local/base/",
        },
    )
    _write_yaml(
        tmp_path / "basic.yaml",
        {"agents": {"brain": {"llm": "llm/or.yaml"}}},
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    config = config_module.load_config("basic.yaml")
    assert config.agents["brain"].llm.site_url == "https://chat-agent.local/base/brain"


def test_openrouter_max_tokens_rejects_zero(monkeypatch, tmp_path: Path):
    """max_tokens=0 should be rejected at config level."""
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {"provider": "openrouter", "model": "test/model", "max_tokens": 0},
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(Exception):
        config_module.resolve_llm_config("llm/x.yaml")


def test_openrouter_reasoning_max_tokens_rejects_below_1024(monkeypatch, tmp_path: Path):
    """reasoning.max_tokens below 1024 should be rejected."""
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "openrouter",
            "model": "test/model",
            "reasoning": {"max_tokens": 512},
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(Exception):
        config_module.resolve_llm_config("llm/x.yaml")
