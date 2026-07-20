"""Deploy Discord prompt guidance for newline-separated emoji endings."""

import shutil
from pathlib import Path

from .base import Migration

_FILES = [
    "builtin-skills/discord-messaging/guide.md",
    "agents/brain/prompts/system.md",
]


class M0124DiscordEmojiNewline(Migration):
    """Deploy Discord DM guidance for standalone emoji ending lines."""

    version = "0.66.1"
    summary = "更新 Discord 規則：句尾顏文字改為另起一行，避免直接黏在正文後面"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
