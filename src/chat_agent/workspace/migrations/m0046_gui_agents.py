"""Add GUI manager and worker agent prompt templates to kernel."""

import shutil
from pathlib import Path

from .base import Migration


class M0046GuiAgents(Migration):
    """Copy GUI agent system prompts into kernel."""

    version = "0.20.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for agent in ("gui_manager", "gui_worker"):
            src = templates_dir / "agents" / agent / "prompts" / "system.md"
            dst = kernel_dir / "agents" / agent / "prompts" / "system.md"
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
