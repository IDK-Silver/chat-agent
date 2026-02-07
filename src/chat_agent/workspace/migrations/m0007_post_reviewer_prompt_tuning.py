"""Migration to tune post-reviewer prompt for conservative compliance checks."""

import shutil
from pathlib import Path

from .base import Migration


class M0007PostReviewerPromptTuning(Migration):
    """Update post_reviewer system prompt with stricter false-positive controls."""

    version = "0.5.1"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"

        if not src.exists():
            return

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
