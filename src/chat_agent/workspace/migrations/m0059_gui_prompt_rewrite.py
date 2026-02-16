"""Rewrite GUI manager system prompt for clarity and accuracy."""

import shutil
from pathlib import Path

from .base import Migration


class M0059GuiPromptRewrite(Migration):
    """Full rewrite of gui_manager system prompt + mismatch/obstructed docs."""

    version = "0.29.0"

    _PROMPT_FILES = [
        "agents/gui_manager/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
