"""Migration to prioritize recent timeline context and natural phrasing."""

import shutil
from pathlib import Path

from .base import Migration


class M0014RecentContextPriority(Migration):
    """Refresh prompts for same-day-first recall and natural response style."""

    version = "0.5.8"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        mappings = [
            (
                templates_dir / "agents" / "brain" / "prompts" / "system.md",
                kernel_dir / "agents" / "brain" / "prompts" / "system.md",
            ),
            (
                templates_dir / "agents" / "pre_reviewer" / "prompts" / "system.md",
                kernel_dir / "agents" / "pre_reviewer" / "prompts" / "system.md",
            ),
            (
                templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md",
                kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md",
            ),
        ]

        for src, dst in mappings:
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
