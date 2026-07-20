"""Migration to add reviewer prompt templates."""

import shutil
from pathlib import Path

from .base import Migration


class M0005ReviewerPrompts(Migration):
    """Add pre-fetch and post-review prompt templates for trigger review system."""

    version = "0.4.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        prompts = templates_dir / "agents" / "brain" / "prompts"
        dst_dir = kernel_dir / "agents" / "brain" / "prompts"

        for name in ("reviewer-pre.md", "reviewer-post.md"):
            src = prompts / name
            dst = dst_dir / name
            if src.exists():
                shutil.copy2(src, dst)
