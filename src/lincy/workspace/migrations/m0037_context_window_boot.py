"""Auto-inject boot files into context and update brain system prompt.

Replaces manual Turn 0 read_file calls with system-level boot context injection.
"""

import shutil
from pathlib import Path

from .base import Migration


class M0037ContextWindowBoot(Migration):
    """Copy updated brain system.md with auto-boot instructions."""

    version = "0.12.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
