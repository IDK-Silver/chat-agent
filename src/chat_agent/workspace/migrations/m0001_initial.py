"""Initial migration - establishes version baseline."""

from pathlib import Path

from .base import Migration


class M0001Initial(Migration):
    """No-op migration for the initial version 0.1.3."""

    version = "0.1.3"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        pass
