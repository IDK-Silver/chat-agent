"""Migration to refresh post-reviewer prompts for ReviewPacket + labels."""

import shutil
from pathlib import Path

from .base import Migration


class M0015PostReviewPacketPrompt(Migration):
    """Copy latest post-reviewer prompts that require label_signals output."""

    version = "0.5.9"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        mappings = [
            (
                templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md",
                kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md",
            ),
            (
                templates_dir / "agents" / "post_reviewer" / "prompts" / "parse-retry.md",
                kernel_dir / "agents" / "post_reviewer" / "prompts" / "parse-retry.md",
            ),
        ]

        for src, dst in mappings:
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
