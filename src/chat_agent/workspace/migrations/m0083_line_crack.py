"""Deploy LINE crack adapter brain prompt changes."""

import shutil
from pathlib import Path

from .base import Migration


class M0083LineCrack(Migration):
    """Update brain prompt with LINE channel guidance."""

    version = "0.49.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents/brain/prompts/system.md"
        dst = kernel_dir / "agents/brain/prompts/system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
