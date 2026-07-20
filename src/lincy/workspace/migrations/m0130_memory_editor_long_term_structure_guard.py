"""Deploy memory_editor prompt with long-term structure guard guidance."""

import shutil
from pathlib import Path

from .base import Migration


class M0130MemoryEditorLongTermStructureGuard(Migration):
    """Copy updated memory_editor prompt for safer long-term writes."""

    version = "0.66.7"
    summary = "更新 memory_editor prompt：限制 long-term append 行為並強化 section 結構規則"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "memory_editor" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "memory_editor" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
