"""GUI scroll position awareness: worker reports scrollbar position,
manager uses it to avoid wasted scrolling."""

import shutil
from pathlib import Path

from .base import Migration


class M0107GuiScrollPosition(Migration):
    version = "0.60.0"
    summary = (
        "GUI prompt 強化：worker 回報 scrollbar 位置，"
        "manager 根據滾動位置決策方向避免無效滾動"
    )

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
