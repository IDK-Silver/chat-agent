"""Deploy Discord builtin skill and route brain prompt to it."""

import shutil
from pathlib import Path

from .base import Migration

_BUILTIN_SKILL_FILES = [
    "builtin-skills/index.md",
    "builtin-skills/discord-messaging/guide.md",
]

_PROMPT_FILES = [
    "agents/brain/prompts/system.md",
]


class M0114DiscordBuiltinSkill(Migration):
    """Publish Discord-specific messaging guidance as a builtin skill."""

    version = "0.63.3"
    summary = "將 Discord 細節規則拆到 builtin skill，Brain prompt 僅保留高層路由與介入判斷"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _BUILTIN_SKILL_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        for rel in _PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
