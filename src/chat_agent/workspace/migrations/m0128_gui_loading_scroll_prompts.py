"""Refresh GUI prompts for loading/progress and scrollbar awareness."""

import shutil
from pathlib import Path

from .base import Migration


class M0128GuiLoadingScrollPrompts(Migration):
    """Copy updated GUI manager/worker prompts into the kernel."""

    version = "0.66.5"
    summary = "更新 GUI prompts：強化 loading/progress bar 與 scrollbar 回報規則"

    _PROMPT_FILES = [
        "agents/gui_manager/prompts/system.md",
        "agents/gui_worker/prompts/system.md",
        "agents/gui_worker/prompts/layout.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
