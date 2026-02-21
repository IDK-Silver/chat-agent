"""Add proactive schedule_action trigger rule to brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0082ScheduleFollowup(Migration):
    """Update brain prompt with follow-up scheduling trigger rule."""

    version = "0.48.2"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents/brain/prompts/system.md"
        dst = kernel_dir / "agents/brain/prompts/system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
