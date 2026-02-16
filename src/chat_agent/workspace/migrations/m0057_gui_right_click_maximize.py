"""Add right-click and maximize_window tools to GUI manager."""

import shutil
from pathlib import Path

from .base import Migration


class M0057GuiRightClickMaximize(Migration):
    """Add right_click, maximize_window tools and usage rules to manager prompt."""

    version = "0.27.0"

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
