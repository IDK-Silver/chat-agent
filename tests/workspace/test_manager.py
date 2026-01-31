"""Tests for WorkspaceManager."""

import pytest
from pathlib import Path
import yaml

from chat_agent.workspace import WorkspaceManager


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

    def test_get_system_prompt(self, tmp_path: Path):
        """get_system_prompt loads and injects working_dir."""
        prompts_dir = tmp_path / "kernel" / "system-prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "brain.md").write_text("Memory at: {working_dir}/memory")

        manager = WorkspaceManager(tmp_path)
        prompt = manager.get_system_prompt("brain")

        assert str(tmp_path) in prompt
        assert "{working_dir}" not in prompt

    def test_get_system_prompt_not_found(self, tmp_path: Path):
        """get_system_prompt raises for missing prompt."""
        (tmp_path / "kernel" / "system-prompts").mkdir(parents=True)

        manager = WorkspaceManager(tmp_path)
        with pytest.raises(FileNotFoundError):
            manager.get_system_prompt("nonexistent")

    def test_get_system_prompt_current_user(self, tmp_path: Path):
        """get_system_prompt injects current_user when present."""
        prompts_dir = tmp_path / "kernel" / "system-prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "brain.md").write_text("User: {current_user}")

        manager = WorkspaceManager(tmp_path)
        prompt = manager.get_system_prompt("brain", current_user="alice")

        assert "alice" in prompt
        assert "{current_user}" not in prompt

    def test_get_system_prompt_current_user_required(self, tmp_path: Path):
        """get_system_prompt raises when current_user is required but missing."""
        prompts_dir = tmp_path / "kernel" / "system-prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "brain.md").write_text("User: {current_user}")

        manager = WorkspaceManager(tmp_path)
        with pytest.raises(ValueError):
            manager.get_system_prompt("brain")

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
