"""Deploy memory_editor prompt with stricter long-term semantic routing."""

import shutil
from pathlib import Path

from .base import Migration


class M0129MemoryEditorLongTermRouting(Migration):
    """Copy updated memory_editor prompt for long-term section routing."""

    version = "0.66.6"
    summary = "更新 memory_editor prompt：強化 long-term 約定/待辦/重要記錄分流規則"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "memory_editor" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "memory_editor" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
