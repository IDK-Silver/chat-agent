"""Deploy send_message response model prompt changes."""

import shutil
from pathlib import Path

from .base import Migration


class M0075SendMessage(Migration):
    """Update brain prompt with send_message tool + inner thoughts model."""

    version = "0.44.0"

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
