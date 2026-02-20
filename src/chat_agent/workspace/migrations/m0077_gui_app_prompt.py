"""Add app_prompt parameter to gui_task + report learning loop."""

import shutil
from pathlib import Path

from .base import Migration


class M0077GuiAppPrompt(Migration):
    """Update brain + gui_manager prompts for app_prompt and report learning."""

    version = "0.45.0"

    _PROMPT_FILES = [
        "agents/brain/prompts/system.md",
        "agents/gui_manager/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
