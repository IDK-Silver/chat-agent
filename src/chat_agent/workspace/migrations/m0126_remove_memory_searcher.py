"""Remove unused memory_searcher prompts from the workspace kernel."""

import shutil
from pathlib import Path

from .base import Migration


class M0126RemoveMemorySearcher(Migration):
    """Delete the retired memory_searcher prompt directory."""

    version = "0.66.3"
    summary = "移除已停用的 memory_searcher kernel prompts，memory_search 僅保留 BM25"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        del templates_dir
        prompt_dir = kernel_dir / "agents" / "memory_searcher"
        if prompt_dir.exists():
            shutil.rmtree(prompt_dir)
