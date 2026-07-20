"""Refresh brain and memory_editor prompts for long-term list semantics."""

import shutil
from pathlib import Path

from .base import Migration


class M0131LongTermLists(Migration):
    """Copy updated prompts that switch long-term from 待辦 to 清單."""

    version = "0.66.8"
    summary = "更新 long-term 語義：改用約定/清單/重要記錄，避免把 long-term 當 task inbox"

    _FILES = [
        "agents/brain/prompts/system.md",
        "agents/memory_editor/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
