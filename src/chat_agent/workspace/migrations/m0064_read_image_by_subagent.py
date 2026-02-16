"""Add read_image_by_subagent tool + updated vision agent prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0064ReadImageBySubagent(Migration):
    """Add read_image_by_subagent tool for brain and update vision agent prompt.

    - Brain prompt: add read_image_by_subagent to tool table, update gui_task guide
    - Vision agent prompt: instruction-oriented rewrite
    """

    version = "0.33.0"

    _PROMPT_FILES = [
        "agents/brain/prompts/system.md",
        "agents/vision/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
