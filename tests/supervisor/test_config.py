"""Tests for supervisor config loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from chat_supervisor.config import load_supervisor_config


def test_load_supervisor_config_resolves_auto_processes(monkeypatch, tmp_path: Path):
    cfgs_dir = tmp_path / "cfgs"
    cfgs_dir.mkdir()
    (cfgs_dir / "supervisor.yaml").write_text(
        """
processes:
  copilot-proxy:
    enabled: auto
    auto_enable_when_any_agent_uses_provider: copilot
    command: ["uv", "run", "copilot-proxy", "serve"]
  claude-code-proxy:
    enabled: auto
    auto_enable_when_any_agent_uses_provider: claude_code
    command: ["uv", "run", "claude-code-proxy", "serve"]
  chat-cli:
    enabled: true
    command: ["uv", "run", "chat-cli"]
    depends_on: ["copilot-proxy", "claude-code-proxy"]
"""
    )
    monkeypatch.setattr("chat_supervisor.config.CFGS_DIR", cfgs_dir)
    monkeypatch.setattr(
        "chat_supervisor.config._used_agent_llm_providers",
        lambda _config_path="agent.yaml": {"claude_code"},
    )

    config = load_supervisor_config("supervisor.yaml")

    assert config.processes["copilot-proxy"].enabled is False
    assert config.processes["claude-code-proxy"].enabled is True
    assert config.processes["chat-cli"].enabled is True


def test_load_supervisor_config_rejects_auto_without_provider(monkeypatch, tmp_path: Path):
    cfgs_dir = tmp_path / "cfgs"
    cfgs_dir.mkdir()
    (cfgs_dir / "supervisor.yaml").write_text(
        """
processes:
  claude-code-proxy:
    enabled: auto
    command: ["uv", "run", "claude-code-proxy", "serve"]
"""
    )
    monkeypatch.setattr("chat_supervisor.config.CFGS_DIR", cfgs_dir)

    with pytest.raises(
        ValueError,
        match="auto_enable_when_any_agent_uses_provider",
    ):
        load_supervisor_config("supervisor.yaml")


def test_used_agent_llm_providers_includes_fallbacks(monkeypatch, tmp_path: Path):
    cfgs_dir = tmp_path / "cfgs"
    cfgs_dir.mkdir()
    (cfgs_dir / "primary.yaml").write_text(
        """
provider: openai
model: gpt-4o
api_key: test-key
"""
    )
    (cfgs_dir / "fallback.yaml").write_text(
        """
provider: openrouter
model: anthropic/claude-sonnet-4.6
api_key: test-key
"""
    )
    (cfgs_dir / "agent.yaml").write_text(
        """
agents:
  brain:
    llm: primary.yaml
    llm_fallbacks:
      - fallback.yaml
"""
    )
    monkeypatch.setattr("chat_supervisor.config.CFGS_DIR", cfgs_dir)
    monkeypatch.setattr("chat_agent.core.config.CFGS_DIR", cfgs_dir)

    from chat_supervisor.config import _used_agent_llm_providers

    assert _used_agent_llm_providers() == {"openai", "openrouter"}
