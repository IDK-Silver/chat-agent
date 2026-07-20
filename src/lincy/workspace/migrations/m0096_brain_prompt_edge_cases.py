"""Refine brain prompt edge-case handling and conflict priority guidance."""

import shutil
from pathlib import Path

from .base import Migration


class M0096BrainPromptEdgeCases(Migration):
    """Deploy brain prompt clarifications for conflicts, no-op, and tool failures."""

    version = "0.57.1"
    summary = "補強 Brain prompt 邊界條件與衝突優先順序（no-op/記憶、HEARTBEAT、排程時區、gui_task 結果處理）"

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
