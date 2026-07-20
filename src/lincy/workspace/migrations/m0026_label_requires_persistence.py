"""Migration to add requires_persistence to post-reviewer label signals."""

import shutil
from pathlib import Path

from .base import Migration


class M0026LabelRequiresPersistence(Migration):
    """Copy updated post_reviewer system prompt with requires_persistence."""

    version = "0.7.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "kernel" / "agents" / "post_reviewer" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
