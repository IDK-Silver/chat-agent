"""Strengthen skills-first principle in brain system prompt.

Ensures agent checks skills/index.md before using execute_shell,
treating skill files as authoritative over built-in knowledge.
"""

import shutil
from pathlib import Path

from .base import Migration


class M0038SkillsFirstShell(Migration):
    """Copy updated brain system.md with skills-first shell protocol."""

    version = "0.13.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
