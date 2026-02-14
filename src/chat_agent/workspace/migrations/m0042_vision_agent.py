"""Add vision agent prompt template to kernel."""

import shutil
from pathlib import Path

from .base import Migration


class M0042VisionAgent(Migration):
    """Copy vision agent system prompt into kernel."""

    version = "0.16.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "vision" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "vision" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
