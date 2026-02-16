"""Human-like browsing rules + intent quality guidance."""

import shutil
from pathlib import Path

from .base import Migration


class M0054GuiHumanBrowsing(Migration):
    """Add web browsing rules to manager, update gui_task tool description."""

    version = "0.26.0"

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
