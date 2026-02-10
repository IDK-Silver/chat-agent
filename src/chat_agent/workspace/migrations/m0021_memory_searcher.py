"""Migration to add memory_searcher agent and update brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0021MemorySearcher(Migration):
    """Add memory_searcher agent prompts and update brain system prompt."""

    version = "0.6.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # Copy memory_searcher prompts
        src_dir = templates_dir / "agents" / "memory_searcher" / "prompts"
        dst_dir = kernel_dir / "agents" / "memory_searcher" / "prompts"
        if src_dir.exists():
            dst_dir.mkdir(parents=True, exist_ok=True)
            for src_file in src_dir.iterdir():
                if src_file.is_file():
                    shutil.copy2(src_file, dst_dir / src_file.name)

        # Update brain system prompt with memory_search tool
        brain_src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        brain_dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if brain_src.exists():
            brain_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(brain_src, brain_dst)
