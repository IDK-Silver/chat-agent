"""Workspace backup utilities for kernel upgrades."""

import shutil
from datetime import datetime
from pathlib import Path


class WorkspaceBackup:
    """Creates full workspace backups before kernel upgrades."""

    def __init__(self, agent_os_dir: Path):
        self.agent_os_dir = agent_os_dir
        self.backups_dir = agent_os_dir / "backups"

    def create_backup(self, current_version: str) -> Path:
        """Backup the entire workspace (excluding backups/ itself).

        Args:
            current_version: Kernel version being backed up.

        Returns:
            Path to the created backup directory.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"v{current_version}_{timestamp}"
        backup_path = self.backups_dir / backup_name

        self.backups_dir.mkdir(parents=True, exist_ok=True)

        for item in self.agent_os_dir.iterdir():
            if item.name == "backups":
                continue
            dest = backup_path / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                backup_path.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)

        return backup_path

    def list_backups(self) -> list[Path]:
        """List all existing backups, newest first."""
        if not self.backups_dir.exists():
            return []
        return sorted(
            [d for d in self.backups_dir.iterdir() if d.is_dir()],
            reverse=True,
        )
