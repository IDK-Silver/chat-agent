"""Remove static memory tree from prompt; add agent/index.md to boot files."""

import shutil
from pathlib import Path

from .base import Migration


class M0095RemoveMemoryTree(Migration):
    """Drop redundant memory directory tree; memory_search provides it dynamically."""

    version = "0.57.0"
    summary = "移除靜態記憶目錄樹，改由 boot file agent/index.md 動態提供記憶區總覽"

    _PROMPT_FILES = [
        "agents/brain/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
