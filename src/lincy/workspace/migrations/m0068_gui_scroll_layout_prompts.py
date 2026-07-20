"""Refresh GUI scroll behavior and layout prompts."""

import shutil
from pathlib import Path

from .base import Migration


class M0068GuiScrollLayoutPrompts(Migration):
    """Copy updated GUI worker layout and manager system prompts."""

    version = "0.37.0"

    _PROMPT_FILES = [
        "agents/gui_worker/prompts/layout.md",
        "agents/gui_manager/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
