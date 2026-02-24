"""Refresh Discord prompt guidance with concrete single-line examples."""

import shutil
from pathlib import Path

from .base import Migration


class M0094DiscordPromptExamples(Migration):
    """Deploy Discord single-line chat examples to reduce newline bundling."""

    version = "0.56.3"
    summary = "加入 Discord 日常聊天單行回覆範例（避免在同一則訊息內用換行假分段）"

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
