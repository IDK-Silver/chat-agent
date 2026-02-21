"""Refine Gmail thread management guidance in brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0081ThreadPromptRefine(Migration):
    """Update brain prompt with three-mode thread management guide."""

    version = "0.48.1"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents/brain/prompts/system.md"
        dst = kernel_dir / "agents/brain/prompts/system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
