"""Refresh memory_editor prompt for temp-memory append-only planning."""

from pathlib import Path
import shutil

from .base import Migration


class M0157TempMemoryAppendOnly(Migration):
    """Deploy prompt rules for temp-memory append-only edits."""

    version = "0.74.15"
    summary = "Memory editor: temp-memory 不讀全文，只允許 append-only 規劃"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "memory_editor" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "memory_editor" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
