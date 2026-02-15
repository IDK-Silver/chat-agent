"""Add multi-intent scanning rules for preference persistence."""

import shutil
from pathlib import Path

from .base import Migration


class M0045MultiIntentPreference(Migration):
    """Copy updated brain + post_reviewer prompts with multi-intent rules."""

    version = "0.19.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        pairs = [
            "agents/brain/prompts/system.md",
            "agents/post_reviewer/prompts/system.md",
        ]
        for rel in pairs:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                shutil.copy2(src, dst)
