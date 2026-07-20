"""Refine Discord builtin skill toward natural grouped-list phrasing."""

import shutil
from pathlib import Path

from .base import Migration

_BUILTIN_SKILL_FILES = [
    "builtin-skills/index.md",
    "builtin-skills/discord-messaging/guide.md",
]


class M0116DiscordNaturalLists(Migration):
    """Deploy more natural schedule/list phrasing for Discord presentation."""

    version = "0.63.5"
    summary = "更新 Discord skill：課表/行程清單改用更自然的人話格式，時間放前面，避免欄位分隔符號"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _BUILTIN_SKILL_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
