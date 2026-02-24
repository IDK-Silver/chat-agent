"""Refresh Discord prompt guidance for single-line casual chat replies."""

import shutil
from pathlib import Path

from .base import Migration


class M0093DiscordPromptSingleLine(Migration):
    """Deploy stricter Discord casual-chat formatting rules."""

    version = "0.56.2"
    summary = "更新 Discord 日常聊天回覆格式（單行短訊息、分多則 send_message、避免用換行假分段）"

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
