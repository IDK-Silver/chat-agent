"""Clarify Discord Markdown behavior in the brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0113DiscordMarkdownPrompt(Migration):
    """Deploy brain prompt guidance for Discord Markdown preservation."""

    version = "0.63.2"
    summary = "更新 Discord prompt：必要時可保留 Markdown 格式，不要先轉成純文字"

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
