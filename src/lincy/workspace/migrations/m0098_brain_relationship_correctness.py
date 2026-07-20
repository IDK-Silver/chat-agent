"""Deploy brain prompt rule prioritizing relationship correctness over fluency."""

import shutil
from pathlib import Path

from .base import Migration


class M0098BrainRelationshipCorrectness(Migration):
    """Update brain system prompt to enforce relationship-consistency checks."""

    version = "0.57.3"
    summary = "強化 Brain 推理優先級：關係正確性（時間線/對象/條件）優先於聊天流暢，不確定先查記憶或澄清"

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
