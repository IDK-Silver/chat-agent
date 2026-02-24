"""Deploy updated brain prompt for Discord adapter + history tool."""

import shutil
from pathlib import Path

from .base import Migration


class M0091DiscordAdapter(Migration):
    """Deploy Discord channel guidance and history tool rules to brain prompt."""

    version = "0.56.0"
    summary = "加入 Discord 頻道指引、history tool 規則與 send_message(reply_to_message) 提示"

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
