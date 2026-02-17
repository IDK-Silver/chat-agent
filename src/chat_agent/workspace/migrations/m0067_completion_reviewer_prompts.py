"""Refresh reviewer prompts for completion-gate design."""

import shutil
from pathlib import Path

from .base import Migration


class M0067CompletionReviewerPrompts(Migration):
    """Copy completion-gate reviewer prompts into workspace kernel."""

    version = "0.36.0"

    _PROMPT_FILES = [
        "agents/post_reviewer/prompts/system.md",
        "agents/post_reviewer/prompts/parse-retry.md",
        "agents/shutdown_reviewer/prompts/system.md",
        "agents/shutdown_reviewer/prompts/parse-retry.md",
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
