"""Migration to update shutdown prompt with skills/interests conditional tasks."""

import shutil
from pathlib import Path

from .base import Migration


class M0004ShutdownV2(Migration):
    """Update shutdown prompt to align with system prompt v0.3.0."""

    version = "0.3.1"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "shutdown.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "shutdown.md"
        if src.exists():
            shutil.copy2(src, dst)
