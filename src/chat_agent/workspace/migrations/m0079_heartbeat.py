"""Add heartbeat + schedule_action support to brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0079Heartbeat(Migration):
    """Update brain prompt with autonomous wake-up and schedule_action docs."""

    version = "0.47.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents/brain/prompts/system.md"
        dst = kernel_dir / "agents/brain/prompts/system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
