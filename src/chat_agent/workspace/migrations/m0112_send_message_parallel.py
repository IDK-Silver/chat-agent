"""Guide parallel send_message calls to prevent short-circuit message loss."""

import shutil
from pathlib import Path

from .base import Migration


class M0112SendMessageParallel(Migration):
    """Deploy brain prompt with parallel send_message guidance."""

    version = "0.63.1"
    summary = "Guide parallel send_message calls to prevent short-circuit message loss"

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
