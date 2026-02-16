"""Force tool call rule in GUI manager prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0055GuiForceToolCall(Migration):
    """Add mandatory tool-call-every-response rule to GUI manager prompt."""

    version = "0.26.1"

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
