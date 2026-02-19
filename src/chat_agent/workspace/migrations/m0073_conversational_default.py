"""Add conversational default section to brain system prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0073ConversationalDefault(Migration):
    """Add core identity section to brain system prompt for conversational default."""

    version = "0.42.0"

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
