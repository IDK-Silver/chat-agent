"""Migration to refresh memory_searcher prompt for two-stage retrieval."""

import shutil
from pathlib import Path

from .base import Migration


class M0031MemorySearchTwoStageConfigurableLimits(Migration):
    """Copy updated memory_searcher prompts."""

    version = "0.9.1"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        relative_paths = [
            "agents/memory_searcher/prompts/system.md",
            "agents/memory_searcher/prompts/parse-retry.md",
        ]
        for relative_path in relative_paths:
            src = templates_dir / relative_path
            dst = kernel_dir / relative_path
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
