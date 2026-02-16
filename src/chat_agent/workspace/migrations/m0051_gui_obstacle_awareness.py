"""Update GUI prompts: worker obstacle reporting, manager tab navigation."""

import shutil
from pathlib import Path

from .base import Migration


class M0051GuiObstacleAwareness(Migration):
    """Add obstruction detection to worker and tab-navigation to manager."""

    version = "0.25.0"

    _PROMPT_FILES = [
        "agents/gui_manager/prompts/system.md",
        "agents/gui_worker/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
