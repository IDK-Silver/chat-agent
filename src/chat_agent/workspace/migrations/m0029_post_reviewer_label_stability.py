"""Migration to tighten post-reviewer label rules for correction/skill signals."""

import shutil
from pathlib import Path

from .base import Migration


class M0029PostReviewerLabelStability(Migration):
    """Copy updated post_reviewer prompt with stricter skill/correction criteria."""

    version = "0.8.1"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
