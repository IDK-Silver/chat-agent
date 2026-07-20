"""Split boot context into system-tier and tool-tier."""

import shutil
from pathlib import Path

from .base import Migration


class M0084BootContextSplit(Migration):
    """Update brain prompt for two-tier boot context."""

    version = "0.50.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents/brain/prompts/system.md"
        dst = kernel_dir / "agents/brain/prompts/system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
