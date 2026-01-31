"""Workspace initialization utilities."""

from importlib import resources
from pathlib import Path
import shutil

from .manager import WorkspaceManager


# Current kernel version (matches templates/kernel/info.yaml)
KERNEL_VERSION = "0.1.1"


class WorkspaceInitializer:
    """Handles workspace creation and upgrades."""

    def __init__(self, manager: WorkspaceManager):
        self.manager = manager

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

    def needs_upgrade(self, target_version: str | None = None) -> bool:
        """Check if kernel upgrade is needed.

        Args:
            target_version: Version to compare against (defaults to KERNEL_VERSION)
        """
        if target_version is None:
            target_version = KERNEL_VERSION

        if not self.manager.is_initialized():
            return True

        current = self.manager.get_kernel_version()
        return current != target_version

    def upgrade_kernel(self) -> None:
        """Upgrade kernel/ directory (preserve memory/).

        Removes old kernel/ and copies new one from templates.
        """
        templates_dir = self._get_templates_dir()

        # Remove old kernel
        if self.manager.kernel_dir.exists():
            shutil.rmtree(self.manager.kernel_dir)

        # Copy new kernel
        kernel_src = templates_dir / "kernel"
        if kernel_src.exists():
            shutil.copytree(kernel_src, self.manager.kernel_dir)

    def _get_templates_dir(self) -> Path:
        """Get the templates directory from package resources."""
        # Use importlib.resources for proper package resource access
        pkg_files = resources.files("chat_agent.workspace")
        # For development, templates are in the same directory
        # Return the path directly since we're in a source layout
        return Path(str(pkg_files)) / "templates"
