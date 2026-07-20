"""Add trigger rule: search people data before external queries."""

import shutil
from pathlib import Path

from .base import Migration


class M0044PeopleSearchTrigger(Migration):
    """Copy updated brain system prompt with people search trigger rules."""

    version = "0.18.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            shutil.copy2(src, dst)
