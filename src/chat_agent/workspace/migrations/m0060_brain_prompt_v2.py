"""Brain system prompt v2: consolidated rules, categorized triggers, people/skills structure."""

import shutil
from pathlib import Path

from .base import Migration


class M0060BrainPromptV2(Migration):
    """Rewrite brain system prompt to v2.

    - Iron rules 11 -> 9 (merge 5+6, 8+9, simplify 10)
    - Trigger rules reorganized into 3 categories (A/B/C)
    - Third-party people support with pinyin naming
    - Skills folder structure (flat .md -> subfolder/index.md)
    - Added read_image, screenshot, gui_task to tool table
    """

    version = "0.30.0"

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
