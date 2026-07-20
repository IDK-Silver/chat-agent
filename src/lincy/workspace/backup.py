"""Workspace backup utilities for kernel upgrades."""

import shutil

from ..timezone_utils import now as tz_now
from pathlib import Path

# Upgrade-managed directories: everything migrations and the upgrade flow
# write to. All small text content. Runtime bulk (state/ media, cache/,
# session/, logs/) is deliberately NOT backed up: copying it is slow, grows
# without bound, and on iCloud-resident workspaces reading dataless media
# files blocks the whole upgrade on on-demand downloads.
_BACKUP_DIRS = {"kernel", "memory", "personal-skills"}

# Directories to skip inside backed-up trees (reproducible content)
_SKIP_DIRS = {"backups", ".venv", "__pycache__", "node_modules"}


_ignore_skip_dirs = shutil.ignore_patterns(*_SKIP_DIRS)


class WorkspaceBackup:
    """Backs up upgrade-managed workspace dirs before kernel upgrades."""

    def __init__(self, agent_os_dir: Path):
        self.agent_os_dir = agent_os_dir
        self.backups_dir = agent_os_dir / "backups"

    def create_backup(self, current_version: str) -> Path:
        """Backup kernel/, memory/ and personal-skills/.

        Args:
            current_version: Kernel version being backed up.

        Returns:
            Path to the created backup directory.
        """
        timestamp = tz_now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"v{current_version}_{timestamp}"
        backup_path = self.backups_dir / backup_name

        self.backups_dir.mkdir(parents=True, exist_ok=True)

        for name in sorted(_BACKUP_DIRS):
            item = self.agent_os_dir / name
            if not item.is_dir():
                continue
            shutil.copytree(item, backup_path / name, ignore=_ignore_skip_dirs)

        return backup_path

    def list_backups(self) -> list[Path]:
        """List all existing backups, newest first."""
        if not self.backups_dir.exists():
            return []
        return sorted(
            [d for d in self.backups_dir.iterdir() if d.is_dir()],
            reverse=True,
        )
