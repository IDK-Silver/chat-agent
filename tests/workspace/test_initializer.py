"""Tests for WorkspaceInitializer."""

import pytest
from pathlib import Path

from chat_agent.workspace import (
    WorkspaceManager,
    WorkspaceInitializer,
    KERNEL_VERSION,
)


class TestWorkspaceInitializer:
    def test_create_structure(self, tmp_path: Path):
        """create_structure creates complete workspace."""
        working_dir = tmp_path / "workspace"
        manager = WorkspaceManager(working_dir)
        initializer = WorkspaceInitializer(manager)

        initializer.create_structure()

        # Check kernel
        assert (working_dir / "kernel" / "info.yaml").exists()
        assert (working_dir / "kernel" / "system-prompts" / "brain.md").exists()
        assert (working_dir / "kernel" / "system-prompts" / "init.md").exists()

        # Check memory
        assert (working_dir / "memory" / "agent" / "index.md").exists()
        assert (working_dir / "memory" / "agent" / "persona.md").exists()
        assert (working_dir / "memory" / "agent" / "inner-state.md").exists()
        assert (working_dir / "memory" / "people" / "index.md").exists()
        assert (working_dir / "memory" / "short-term.md").exists()

    def test_create_structure_idempotent(self, tmp_path: Path):
        """create_structure does nothing if already initialized."""
        manager = WorkspaceManager(tmp_path)

        # Manually create info.yaml
        (tmp_path / "kernel").mkdir()
        (tmp_path / "kernel" / "info.yaml").write_text("version: '0.1.0'")

        initializer = WorkspaceInitializer(manager)
        initializer.create_structure()  # Should not raise or overwrite

        # Memory should not be created
        assert not (tmp_path / "memory").exists()

    def test_needs_upgrade_not_initialized(self, tmp_path: Path):
        """needs_upgrade returns True for uninitialized workspace."""
        manager = WorkspaceManager(tmp_path)
        initializer = WorkspaceInitializer(manager)

        assert initializer.needs_upgrade() is True

    def test_needs_upgrade_same_version(self, tmp_path: Path):
        """needs_upgrade returns False for current version."""
        (tmp_path / "kernel").mkdir()
        (tmp_path / "kernel" / "info.yaml").write_text(f"version: '{KERNEL_VERSION}'")

        manager = WorkspaceManager(tmp_path)
        initializer = WorkspaceInitializer(manager)

        assert initializer.needs_upgrade() is False

    def test_needs_upgrade_old_version(self, tmp_path: Path):
        """needs_upgrade returns True for old version."""
        (tmp_path / "kernel").mkdir()
        (tmp_path / "kernel" / "info.yaml").write_text("version: '0.0.1'")

        manager = WorkspaceManager(tmp_path)
        initializer = WorkspaceInitializer(manager)

        assert initializer.needs_upgrade() is True

    def test_upgrade_kernel_preserves_memory(self, tmp_path: Path):
        """upgrade_kernel replaces kernel but keeps memory."""
        # Setup initial state
        kernel_dir = tmp_path / "kernel"
        kernel_dir.mkdir()
        (kernel_dir / "info.yaml").write_text("version: '0.0.1'")
        (kernel_dir / "old_file.txt").write_text("old")

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "user_data.md").write_text("precious data")

        manager = WorkspaceManager(tmp_path)
        initializer = WorkspaceInitializer(manager)

        initializer.upgrade_kernel()

        # Memory preserved
        assert (memory_dir / "user_data.md").read_text() == "precious data"

        # Kernel upgraded
        assert not (kernel_dir / "old_file.txt").exists()
        assert manager.get_kernel_version() == KERNEL_VERSION
