"""GUI scan_layout tool + brain screenshot region support."""

import shutil
from pathlib import Path

from .base import Migration


class M0063GuiScanLayout(Migration):
    """Add scan_layout tool for GUI manager and screenshot region for brain.

    - New gui_worker layout.md prompt for structured GUI layout analysis
    - Updated gui_manager prompt: scan_layout tool + revised workflow
    - Updated brain prompt: screenshot region param, gui_task read_image note
    """

    version = "0.32.0"

    _PROMPT_FILES = [
        "agents/gui_worker/prompts/layout.md",
        "agents/gui_manager/prompts/system.md",
        "agents/brain/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
