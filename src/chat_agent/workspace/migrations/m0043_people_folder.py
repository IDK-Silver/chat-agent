"""Restructure people memory: user-{id}.md -> {id}/index.md folder layout."""

import shutil
from pathlib import Path

from .base import Migration


class M0043PeopleFolder(Migration):
    """Copy updated prompt templates for people folder structure."""

    version = "0.17.0"

    _PROMPT_FILES = [
        ("brain", "system.md"),
        ("brain", "shutdown.md"),
        ("post_reviewer", "system.md"),
        ("shutdown_reviewer", "system.md"),
        ("memory_searcher", "system.md"),
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for agent, filename in self._PROMPT_FILES:
            src = templates_dir / "agents" / agent / "prompts" / filename
            dst = kernel_dir / "agents" / agent / "prompts" / filename
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
