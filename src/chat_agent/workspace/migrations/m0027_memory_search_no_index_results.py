"""Migration to refresh memory_searcher prompt and disallow index.md outputs."""

import shutil
from pathlib import Path

from .base import Migration


class M0027MemorySearchNoIndexResults(Migration):
    """Copy latest memory_searcher system prompt."""

    version = "0.7.1"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "memory_searcher" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "memory_searcher" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
