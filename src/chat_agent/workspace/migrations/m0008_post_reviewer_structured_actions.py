"""Migration to update post-reviewer prompt for structured action output."""

import shutil
from pathlib import Path

from .base import Migration


class M0008PostReviewerStructuredActions(Migration):
    """Refresh post-reviewer prompt with structured required_actions contract."""

    version = "0.5.2"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"

        if not src.exists():
            return

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
