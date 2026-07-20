"""Migration to add parse-retry prompts for reviewer agents."""

import shutil
from pathlib import Path

from .base import Migration


class M0010ReviewerParseRetryPrompts(Migration):
    """Copy parse-retry prompt templates for all reviewer agents."""

    version = "0.5.4"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        mappings = [
            (
                templates_dir / "agents" / "pre_reviewer" / "prompts" / "parse-retry.md",
                kernel_dir / "agents" / "pre_reviewer" / "prompts" / "parse-retry.md",
            ),
            (
                templates_dir / "agents" / "post_reviewer" / "prompts" / "parse-retry.md",
                kernel_dir / "agents" / "post_reviewer" / "prompts" / "parse-retry.md",
            ),
            (
                templates_dir / "agents" / "shutdown_reviewer" / "prompts" / "parse-retry.md",
                kernel_dir / "agents" / "shutdown_reviewer" / "prompts" / "parse-retry.md",
            ),
        ]

        for src, dst in mappings:
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
