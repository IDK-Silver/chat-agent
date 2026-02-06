"""Tests for WorkspaceManager."""

import pytest
from pathlib import Path
import yaml

from chat_agent.workspace import WorkspaceManager


def _create_agent_prompt(tmp_path: Path, agent: str, prompt: str, content: str) -> None:
    """Helper to create an agent prompt file."""
    d = tmp_path / "kernel" / "agents" / agent / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{prompt}.md").write_text(content)


class TestWorkspaceManager:
    def test_is_initialized_false(self, tmp_path: Path):
        """is_initialized returns False for empty directory."""
        manager = WorkspaceManager(tmp_path)
        assert manager.is_initialized() is False

    def test_is_initialized_true(self, tmp_path: Path):
        """is_initialized returns True when info.yaml exists."""
        (tmp_path / "kernel").mkdir()
        (tmp_path / "kernel" / "info.yaml").write_text("version: '0.1.0'")

        manager = WorkspaceManager(tmp_path)
        assert manager.is_initialized() is True

    def test_get_kernel_version(self, tmp_path: Path):
        """get_kernel_version reads version from info.yaml."""
        kernel = tmp_path / "kernel"
        kernel.mkdir()
        (kernel / "info.yaml").write_text(yaml.dump({"version": "1.2.3"}))

        manager = WorkspaceManager(tmp_path)
        assert manager.get_kernel_version() == "1.2.3"

    def test_get_kernel_version_not_initialized(self, tmp_path: Path):
        """get_kernel_version raises for uninitialized workspace."""
        manager = WorkspaceManager(tmp_path)
        with pytest.raises(FileNotFoundError):
            manager.get_kernel_version()

    # --- get_agent_prompt ---

    def test_get_agent_prompt(self, tmp_path: Path):
        """get_agent_prompt loads and injects working_dir."""
        _create_agent_prompt(tmp_path, "brain", "system", "Memory at: {working_dir}/memory")

        manager = WorkspaceManager(tmp_path)
        prompt = manager.get_agent_prompt("brain", "system")

        assert str(tmp_path) in prompt
        assert "{working_dir}" not in prompt

    def test_get_agent_prompt_current_user(self, tmp_path: Path):
        """get_agent_prompt injects current_user."""
        _create_agent_prompt(tmp_path, "brain", "shutdown", "User: {current_user}")

        manager = WorkspaceManager(tmp_path)
        prompt = manager.get_agent_prompt("brain", "shutdown", current_user="alice")

        assert "alice" in prompt
        assert "{current_user}" not in prompt

    def test_get_agent_prompt_current_user_required(self, tmp_path: Path):
        """get_agent_prompt raises when current_user needed but missing."""
        _create_agent_prompt(tmp_path, "brain", "shutdown", "User: {current_user}")

        manager = WorkspaceManager(tmp_path)
        with pytest.raises(ValueError):
            manager.get_agent_prompt("brain", "shutdown")

    def test_get_agent_prompt_date(self, tmp_path: Path):
        """get_agent_prompt injects today's date."""
        _create_agent_prompt(tmp_path, "brain", "shutdown", "Date: {date}")

        manager = WorkspaceManager(tmp_path)
        prompt = manager.get_agent_prompt("brain", "shutdown")

        assert "{date}" not in prompt
        # Should be ISO format YYYY-MM-DD
        from datetime import date
        assert date.today().isoformat() in prompt

    def test_get_agent_prompt_not_found(self, tmp_path: Path):
        """get_agent_prompt raises for missing prompt."""
        (tmp_path / "kernel" / "agents").mkdir(parents=True)

        manager = WorkspaceManager(tmp_path)
        with pytest.raises(FileNotFoundError):
            manager.get_agent_prompt("brain", "nonexistent")

    # --- get_system_prompt (new structure) ---

    def test_get_system_prompt(self, tmp_path: Path):
        """get_system_prompt loads from new agents/ structure."""
        _create_agent_prompt(tmp_path, "brain", "system", "Memory at: {working_dir}/memory")

        manager = WorkspaceManager(tmp_path)
        prompt = manager.get_system_prompt("brain")

        assert str(tmp_path) in prompt
        assert "{working_dir}" not in prompt

    def test_get_system_prompt_not_found(self, tmp_path: Path):
        """get_system_prompt raises for missing prompt."""
        (tmp_path / "kernel" / "agents").mkdir(parents=True)
        (tmp_path / "kernel" / "system-prompts").mkdir(parents=True)

        manager = WorkspaceManager(tmp_path)
        with pytest.raises(FileNotFoundError):
            manager.get_system_prompt("nonexistent")

    def test_get_system_prompt_current_user(self, tmp_path: Path):
        """get_system_prompt injects current_user."""
        _create_agent_prompt(tmp_path, "brain", "system", "User: {current_user}")

        manager = WorkspaceManager(tmp_path)
        prompt = manager.get_system_prompt("brain", current_user="alice")

        assert "alice" in prompt
        assert "{current_user}" not in prompt

    def test_get_system_prompt_current_user_required(self, tmp_path: Path):
        """get_system_prompt raises when current_user required but missing."""
        _create_agent_prompt(tmp_path, "brain", "system", "User: {current_user}")

        manager = WorkspaceManager(tmp_path)
        with pytest.raises(ValueError):
            manager.get_system_prompt("brain")

    # --- get_system_prompt (legacy fallback) ---

    def test_get_system_prompt_legacy_fallback(self, tmp_path: Path):
        """get_system_prompt falls back to legacy system-prompts/ dir."""
        prompts_dir = tmp_path / "kernel" / "system-prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "brain.md").write_text("Legacy: {working_dir}/memory")

        manager = WorkspaceManager(tmp_path)
        prompt = manager.get_system_prompt("brain")

        assert str(tmp_path) in prompt
        assert "{working_dir}" not in prompt

    # --- resolve_memory_path ---

    def test_resolve_memory_path(self, tmp_path: Path):
        """resolve_memory_path resolves relative paths."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        manager = WorkspaceManager(tmp_path)
        result = manager.resolve_memory_path("agent/persona.md")

        assert result == memory_dir / "agent" / "persona.md"

    def test_resolve_memory_path_escape(self, tmp_path: Path):
        """resolve_memory_path blocks path traversal."""
        (tmp_path / "memory").mkdir()

        manager = WorkspaceManager(tmp_path)
        with pytest.raises(ValueError, match="escapes"):
            manager.resolve_memory_path("../kernel/info.yaml")
