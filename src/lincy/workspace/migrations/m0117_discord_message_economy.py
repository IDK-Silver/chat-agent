"""Tighten Discord message-splitting guidance to avoid same-turn duplicates."""

import shutil
from pathlib import Path

from .base import Migration

_FILES = [
    "builtin-skills/discord-messaging/guide.md",
    "agents/brain/prompts/system.md",
]


class M0117DiscordMessageEconomy(Migration):
    """Deploy Discord/brain prompt guidance for message economy."""

    version = "0.63.6"
    summary = "收緊 Discord 分段規則：保留短訊風格，但避免同輪把同一問題改寫成多則"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
