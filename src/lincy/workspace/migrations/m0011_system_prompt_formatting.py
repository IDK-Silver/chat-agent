"""Migration to add response formatting guidance to brain system prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0011SystemPromptFormatting(Migration):
    """Update brain system prompt with response formatting guidance."""

    version = "0.5.5"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            shutil.copy2(src, dst)
