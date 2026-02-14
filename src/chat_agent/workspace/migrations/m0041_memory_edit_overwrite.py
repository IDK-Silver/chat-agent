"""Add overwrite operation to memory_editor planner prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0041MemoryEditOverwrite(Migration):
    """Copy updated memory_editor system prompt with overwrite kind."""

    version = "0.15.1"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "memory_editor" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "memory_editor" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
