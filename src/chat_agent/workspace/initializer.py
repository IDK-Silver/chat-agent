"""Workspace initialization utilities."""

from importlib import resources
from pathlib import Path
import shutil

from .backup import WorkspaceBackup
from .manager import WorkspaceManager
from .migrator import Migrator


class WorkspaceInitializer:
    """Handles workspace creation and upgrades."""

    def __init__(self, manager: WorkspaceManager):
        self.manager = manager
        self._migrator: Migrator | None = None

    @property
    def migrator(self) -> Migrator:
        """Lazy-loaded migrator instance."""
        if self._migrator is None:
            templates_dir = self._get_templates_dir()
            self._migrator = Migrator(
                self.manager.kernel_dir,
                templates_dir / "kernel",
            )
        return self._migrator

    def create_structure(self) -> None:
        """Copy templates to working_dir (kernel + memory).

        Creates the complete directory structure from package templates.
        Does nothing if workspace already exists.
        """
        if self.manager.is_initialized():
            return

        # Get templates directory from package resources
        templates_dir = self._get_templates_dir()

        # Create working directory
        self.manager.working_dir.mkdir(parents=True, exist_ok=True)

        # Copy kernel/ (always overwrite)
        kernel_src = templates_dir / "kernel"
        if kernel_src.exists():
            shutil.copytree(kernel_src, self.manager.kernel_dir)

        # Copy memory/ (only if not exists)
        if not self.manager.memory_dir.exists():
            memory_src = templates_dir / "memory"
            if memory_src.exists():
                shutil.copytree(memory_src, self.manager.memory_dir)

    def needs_upgrade(self) -> bool:
        """Check if kernel upgrade is needed."""
        if not self.manager.is_initialized():
            return True
        return self.migrator.needs_migration()

    def upgrade_kernel(self) -> list[str]:
        """Run pending migrations.

        Creates a full workspace backup before applying any migration.

        Returns:
            List of applied version strings.
        """
        backup = WorkspaceBackup(self.manager.working_dir)
        current_version = self.manager.get_kernel_version()
        backup.create_backup(current_version)

        return self.migrator.run_migrations()

    def _get_templates_dir(self) -> Path:
        """Get the templates directory from package resources."""
        # Use importlib.resources for proper package resource access
        pkg_files = resources.files("chat_agent.workspace")
        # For development, templates are in the same directory
        # Return the path directly since we're in a source layout
        return Path(str(pkg_files)) / "templates"
