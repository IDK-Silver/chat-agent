"""Deploy Discord DM single-line prompt guidance."""

import shutil
from pathlib import Path

from .base import Migration

_FILES = [
    "builtin-skills/discord-messaging/guide.md",
    "agents/brain/prompts/system.md",
]


class M0119DiscordDmSingleLine(Migration):
    """Deploy Discord DM prompt guidance favoring one-line messages."""

    version = "0.63.8"
    summary = "更新 Discord DM 行程規則：預設拆成多則單行訊息，不在單一訊息內用換行排版"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
