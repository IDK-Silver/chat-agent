"""Brain prompt: add gui_task usage guidance for sub-agent delegation."""

import shutil
from pathlib import Path

from .base import Migration


class M0061GuiTaskGuidance(Migration):
    """Add gui_task usage guidance to brain system prompt.

    - Sub-agent has no conversation context, intent must be self-contained
    - Plan steps before delegating, list concrete UI operations
    - No need to specify screenshot save path; capture_screenshot returns path automatically
    - Use read_image on returned screenshot path for visual info
    """

    version = "0.31.0"

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
