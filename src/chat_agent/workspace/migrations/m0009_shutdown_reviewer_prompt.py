"""Migration to add shutdown reviewer prompt template."""

import shutil
from pathlib import Path

from .base import Migration


class M0009ShutdownReviewerPrompt(Migration):
    """Add shutdown_reviewer system prompt to kernel agents."""

    version = "0.5.3"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "shutdown_reviewer" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "shutdown_reviewer" / "prompts" / "system.md"

        if not src.exists():
            return

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
