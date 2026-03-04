"""Refresh brain prompt for send_message single-body refactor."""

import shutil
from pathlib import Path

from .base import Migration


class M0111SendMessageSingleBody(Migration):
    """Deploy updated brain prompt with single-body send_message."""

    version = "0.63.0"
    summary = "send_message segments -> top-level body; multi-message via multiple calls"

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
