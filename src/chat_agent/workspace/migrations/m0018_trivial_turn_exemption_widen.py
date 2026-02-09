"""Migration to widen trivial turn exemption in post-reviewer prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0018TrivialTurnExemptionWiden(Migration):
    """Copy updated post_reviewer prompt with broader trivial turn exemption."""

    version = "0.5.12"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
