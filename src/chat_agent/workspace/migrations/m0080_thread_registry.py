"""Add thread registry support and update brain prompt with thread guidance."""

import shutil
from pathlib import Path

from .base import Migration


class M0080ThreadRegistry(Migration):
    """Update brain prompt with Gmail thread continuation guidance."""

    version = "0.48.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents/brain/prompts/system.md"
        dst = kernel_dir / "agents/brain/prompts/system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
