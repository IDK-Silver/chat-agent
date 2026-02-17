"""Add progress reviewer prompt templates."""

import shutil
from pathlib import Path

from .base import Migration


class M0066ProgressReviewer(Migration):
    """Copy progress reviewer prompts into workspace kernel."""

    version = "0.35.0"

    _PROMPT_FILES = [
        "agents/progress_reviewer/prompts/system.md",
        "agents/progress_reviewer/prompts/parse-retry.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
