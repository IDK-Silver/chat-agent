"""Migration to convert memory_searcher prompts to Traditional Chinese."""

import shutil
from pathlib import Path

from .base import Migration


class M0033MemorySearchZhTw(Migration):
    """Copy updated memory_searcher prompts with zh-TW and recall/precision guidance."""

    version = "0.10.1"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src_dir = templates_dir / "agents" / "memory_searcher" / "prompts"
        dst_dir = kernel_dir / "agents" / "memory_searcher" / "prompts"
        if src_dir.exists():
            dst_dir.mkdir(parents=True, exist_ok=True)
            for src_file in src_dir.iterdir():
                if src_file.is_file():
                    shutil.copy2(src_file, dst_dir / src_file.name)
