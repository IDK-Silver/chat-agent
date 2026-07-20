"""Refresh Discord builtin skill with semantic presentation guidance."""

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


class M0115DiscordPresentationStrategy(Migration):
    """Deploy Discord presentation strategy updates to existing workspaces."""

    version = "0.63.4"
    summary = "更新 Discord skill：行程/課表優先語義整理，table 不再是主輸出路徑"

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
