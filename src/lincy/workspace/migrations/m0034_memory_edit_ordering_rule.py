"""Migration to add memory operation ordering rule to brain system prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0034MemoryEditOrderingRule(Migration):
    """Copy updated brain system prompt with iron rule #6 (operation ordering)."""

    version = "0.10.2"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
