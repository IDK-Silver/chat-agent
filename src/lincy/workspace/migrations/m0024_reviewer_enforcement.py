"""Migration to strengthen reviewer enforcement, rename memory_writer to memory_editor."""

import shutil
from pathlib import Path

from .base import Migration


class M0024ReviewerEnforcement(Migration):
    """Update prompts and rename memory_writer agent to memory_editor."""

    version = "0.6.3"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # Copy updated prompts
        pairs = [
            ("agents/brain/prompts/system.md", "agents/brain/prompts/system.md"),
            (
                "agents/post_reviewer/prompts/system.md",
                "agents/post_reviewer/prompts/system.md",
            ),
        ]
        for rel_src, rel_dst in pairs:
            src = templates_dir / rel_src
            dst = kernel_dir / rel_dst
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # Rename memory_writer -> memory_editor in kernel
        old_dir = kernel_dir / "agents" / "memory_writer"
        new_dir = kernel_dir / "agents" / "memory_editor"
        if old_dir.exists() and not new_dir.exists():
            old_dir.rename(new_dir)
