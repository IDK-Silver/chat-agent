"""Tests for reasoning profile validation during config load."""

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


def test_resolve_llm_config_fails_when_reasoning_has_no_capabilities(
    monkeypatch,
    tmp_path: Path,
):
    llm_path = tmp_path / "llm" / "x.yaml"
    _write_yaml(
        llm_path,
        {
            "provider": "ollama",
            "model": "test-model",
            "reasoning": {"enabled": True},
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(ValueError, match="capabilities.reasoning is missing"):
        config_module.resolve_llm_config("llm/x.yaml")


def test_resolve_llm_config_fails_on_unsupported_effort(monkeypatch, tmp_path: Path):
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


def test_resolve_llm_config_fails_on_unsupported_max_tokens(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "x.yaml",
        {
            "provider": "openrouter",
            "model": "provider/model",
            "api_key": "test-key",
            "reasoning": {"max_tokens": 128},
            "capabilities": {
                "reasoning": {
                    "supports_toggle": True,
                    "supported_efforts": ["low", "medium", "high"],
                    "supports_max_tokens": False,
                }
            },
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(ValueError, match="supports_max_tokens=false"):
        config_module.resolve_llm_config("llm/x.yaml")


def test_load_config_fails_fast_on_openai_reasoning_max_tokens(monkeypatch, tmp_path: Path):
    _write_yaml(
        tmp_path / "llm" / "openai" / "profile.yaml",
        {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test-key",
            "reasoning": {"enabled": True, "max_tokens": 128},
            "capabilities": {
                "reasoning": {
                    "supports_toggle": True,
                    "supported_efforts": ["low", "medium", "high"],
                    "supports_max_tokens": True,
                }
            },
        },
    )
    _write_yaml(
        tmp_path / "basic.yaml",
        {
            "agents": {
                "brain": {"llm": "llm/openai/profile.yaml"},
            }
        },
    )
    monkeypatch.setattr(config_module, "CFGS_DIR", tmp_path)

    with pytest.raises(ValueError, match="OpenAI mapping does not support reasoning.max_tokens"):
        config_module.load_config("basic.yaml")
