"""gui_task runs in background: async execution, result via queue."""

import shutil
from pathlib import Path

from .base import Migration


class M0108GuiTaskBackground(Migration):
    version = "0.61.0"
    summary = (
        "gui_task 改為非同步背景執行：呼叫立即回傳，"
        "結果以 [gui, from system] 訊息送回"
    )

    _PROMPT_FILES = [
        "agents/brain/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
