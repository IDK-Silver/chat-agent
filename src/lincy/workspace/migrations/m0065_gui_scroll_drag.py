"""Add scroll and drag tools to GUI manager."""

import shutil
from pathlib import Path

from .base import Migration


class M0065GuiScrollDrag(Migration):
    """Add scroll and drag tools to GUI manager.

    - Manager prompt: add scroll/drag tool descriptions, update scrolling rules,
      add drag operation rules, update common mistakes.
    """

    version = "0.34.0"

    _PROMPT_FILES = [
        "agents/gui_manager/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
