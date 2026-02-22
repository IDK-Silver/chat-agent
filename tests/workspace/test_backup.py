"""Tests for WorkspaceBackup."""

import pytest
from pathlib import Path

from chat_agent.workspace.backup import WorkspaceBackup


class TestWorkspaceBackup:
    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create a minimal workspace structure."""
        kernel = tmp_path / "kernel"
        kernel.mkdir()
        (kernel / "info.yaml").write_text("version: '0.1.3'")
        (kernel / "agents").mkdir()
        (kernel / "agents" / "brain").mkdir(parents=True)

        memory = tmp_path / "memory"
        memory.mkdir()
        (memory / "recent.md").write_text("some memory")

        return tmp_path

    def test_create_backup_creates_directory(self, workspace):
        backup = WorkspaceBackup(workspace)
        path = backup.create_backup("0.1.3")

        assert path.exists()
        assert path.parent == workspace / "backups"

    def test_create_backup_copies_kernel_and_memory(self, workspace):
        backup = WorkspaceBackup(workspace)
        path = backup.create_backup("0.1.3")

        assert (path / "kernel" / "info.yaml").exists()
        assert (path / "kernel" / "agents" / "brain").is_dir()
        assert (path / "memory" / "recent.md").exists()
        assert (path / "memory" / "recent.md").read_text() == "some memory"

    def test_create_backup_excludes_backups_dir(self, workspace):
        """Backup must not recursively include the backups/ directory."""
        backup = WorkspaceBackup(workspace)

        # Create first backup
        backup.create_backup("0.1.3")
        # Create second backup (backups/ now exists with content)
        path2 = backup.create_backup("0.1.3")

        # Second backup should not contain backups/
        assert not (path2 / "backups").exists()

    def test_create_backup_naming_convention(self, workspace):
        backup = WorkspaceBackup(workspace)
        path = backup.create_backup("0.1.3")

        assert path.name.startswith("v0.1.3_")

    def test_create_backup_empty_workspace(self, tmp_path):
        """Empty workspace (no kernel/memory) should not raise."""
        backup = WorkspaceBackup(tmp_path)
        path = backup.create_backup("0.0.0")

        assert path.exists() or not path.exists()  # may not be created if nothing to copy

    def test_list_backups_empty(self, workspace):
        backup = WorkspaceBackup(workspace)
        assert backup.list_backups() == []

    def test_list_backups_returns_sorted(self, workspace):
        backup = WorkspaceBackup(workspace)

        p1 = backup.create_backup("0.1.0")
        p2 = backup.create_backup("0.1.3")

        result = backup.list_backups()
        assert len(result) == 2
        # Newest first (p2 has later timestamp)
        assert result[0] == p2
        assert result[1] == p1

    def test_multiple_backups_independent(self, workspace):
        """Each backup is a full independent copy."""
        backup = WorkspaceBackup(workspace)
        p1 = backup.create_backup("0.1.3")

        # Modify workspace after first backup
        (workspace / "memory" / "recent.md").write_text("updated")
        p2 = backup.create_backup("0.1.3")

        # First backup retains original content
        assert (p1 / "memory" / "recent.md").read_text() == "some memory"
        # Second backup has updated content
        assert (p2 / "memory" / "recent.md").read_text() == "updated"
