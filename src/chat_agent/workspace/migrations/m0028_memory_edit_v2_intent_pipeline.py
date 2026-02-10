"""Migration to add memory_editor prompts and refresh brain memory_edit contract."""

import shutil
from pathlib import Path

from .base import Migration


class M0028MemoryEditV2IntentPipeline(Migration):
    """Deploy memory_edit v2 prompts (instruction contract + memory_editor planner)."""

    version = "0.8.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # Add memory_editor prompts
        src_dir = templates_dir / "agents" / "memory_editor" / "prompts"
        dst_dir = kernel_dir / "agents" / "memory_editor" / "prompts"
        if src_dir.exists():
            dst_dir.mkdir(parents=True, exist_ok=True)
            for src_file in src_dir.iterdir():
                if src_file.is_file():
                    shutil.copy2(src_file, dst_dir / src_file.name)

        # Refresh brain prompts to v2 memory_edit contract
        pairs = [
            ("agents/brain/prompts/system.md", "agents/brain/prompts/system.md"),
            ("agents/brain/prompts/shutdown.md", "agents/brain/prompts/shutdown.md"),
        ]
        for rel_src, rel_dst in pairs:
            src = templates_dir / rel_src
            dst = kernel_dir / rel_dst
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
