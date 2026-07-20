"""Refresh brain prompt to enforce send_message segments contract."""

import shutil
from pathlib import Path

from .base import Migration


class M0110BrainSendMessageSegments(Migration):
    """Deploy updated brain prompt with segments-only send_message examples."""

    version = "0.62.1"
    summary = "更新 Brain 系統提示：send_message 範例全面改為 segments 參數格式"

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
