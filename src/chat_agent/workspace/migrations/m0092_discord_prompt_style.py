"""Refresh Discord conversation style guidance in brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0092DiscordPromptStyle(Migration):
    """Deploy Discord reply style rules for shorter, human-like messages."""

    version = "0.56.1"
    summary = "更新 Discord 回覆風格規則（先答重點、短句分段、避免一則全包）"

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
