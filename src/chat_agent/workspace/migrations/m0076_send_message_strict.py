"""Strengthen send_message prompt: text output is completely invisible."""

import shutil
from pathlib import Path

from .base import Migration


class M0076SendMessageStrict(Migration):
    """Reinforce that text output is invisible; all content must go via send_message."""

    version = "0.44.1"

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
