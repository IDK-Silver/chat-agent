"""Add tool-immediate iron rule to brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0056BrainToolImmediate(Migration):
    """Add iron rule 11: tool calls must happen in the same turn, never deferred."""

    version = "0.26.2"

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
