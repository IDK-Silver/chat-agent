"""Strengthen per-turn memory sync motivation in brain system prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0070MemorySyncPrompt(Migration):
    """Update brain system prompt with memory persistence motivation."""

    version = "0.39.0"

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
