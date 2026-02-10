"""Migration to add empty_reply violation to post-reviewer prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0020EmptyReplyViolation(Migration):
    """Copy updated post_reviewer prompt with empty_reply violation."""

    version = "0.5.14"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
