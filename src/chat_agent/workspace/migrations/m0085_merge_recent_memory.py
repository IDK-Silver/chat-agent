"""Merge inner-state.md + short-term.md into recent.md."""

import shutil
from pathlib import Path

from .base import Migration

_PROMPT_COPIES = [
    ("agents/brain/prompts/system.md", "agents/brain/prompts/system.md"),
    ("agents/memory_editor/prompts/system.md", "agents/memory_editor/prompts/system.md"),
    ("agents/init/prompts/system.md", "agents/init/prompts/system.md"),
]


class M0085MergeRecentMemory(Migration):
    """Merge inner-state.md and short-term.md into recent.md."""

    version = "0.51.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # Copy updated prompts
        for src_rel, dst_rel in _PROMPT_COPIES:
            src = templates_dir / src_rel
            dst = kernel_dir / dst_rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # Create recent.md template in memory if it does not exist.
        # The memory dir is a sibling of kernel_dir.
        memory_dir = kernel_dir.parent / "memory" / "agent"
        recent = memory_dir / "recent.md"
        if not recent.exists() and memory_dir.is_dir():
            recent.write_text("# 近期記憶\n\n", encoding="utf-8")

        # Update agent/index.md from template
        src_index = templates_dir.parent / "memory" / "agent" / "index.md"
        dst_index = memory_dir / "index.md"
        if src_index.exists() and dst_index.exists():
            shutil.copy2(src_index, dst_index)
