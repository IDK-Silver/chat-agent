"""Migration to refresh prompts for memory_edit replace_block support."""

import shutil
from pathlib import Path

from .base import Migration


class M0016ReplaceBlockPromptUpdate(Migration):
    """Copy latest brain/memory-writer prompts with replace_block contract."""

    version = "0.5.10"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        mappings = [
            (
                templates_dir / "agents" / "brain" / "prompts" / "system.md",
                kernel_dir / "agents" / "brain" / "prompts" / "system.md",
            ),
            (
                templates_dir / "agents" / "memory_writer" / "prompts" / "system.md",
                kernel_dir / "agents" / "memory_writer" / "prompts" / "system.md",
            ),
            (
                templates_dir / "agents" / "memory_writer" / "prompts" / "parse-retry.md",
                kernel_dir / "agents" / "memory_writer" / "prompts" / "parse-retry.md",
            ),
        ]

        for src, dst in mappings:
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
