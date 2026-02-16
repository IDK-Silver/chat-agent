"""Escalate unautomatable verification steps in GUI manager."""

import shutil
from pathlib import Path

from .base import Migration


class M0052GuiUnautomatableEscalation(Migration):
    """Add escalation rule for QR code, SMS, biometric verification."""

    version = "0.25.1"

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
