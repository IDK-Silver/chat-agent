"""Migration to add simulated_user_turn and gender_confusion violations."""

import shutil
from pathlib import Path

from .base import Migration


class M0019ReviewPacketViolations(Migration):
    """Copy updated post_reviewer prompt with new violation types."""

    version = "0.5.13"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
