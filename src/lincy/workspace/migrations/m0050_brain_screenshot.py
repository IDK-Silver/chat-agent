"""Brain screenshot tool: pure code change, version bump only."""

from pathlib import Path

from .base import Migration


class M0050BrainScreenshot(Migration):
    """Add screenshot tool for brain agent (code-only, no template changes)."""

    version = "0.24.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        pass
