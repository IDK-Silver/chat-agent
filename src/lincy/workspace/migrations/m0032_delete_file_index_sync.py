"""Migration to add delete_file operation and index sync rules."""

import shutil
from pathlib import Path

from .base import Migration


class M0032DeleteFileIndexSync(Migration):
    """Copy updated memory_editor, post_reviewer, and brain prompts."""

    version = "0.10.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        relative_paths = [
            "agents/memory_editor/prompts/system.md",
            "agents/post_reviewer/prompts/system.md",
            "agents/brain/prompts/system.md",
        ]
        for relative_path in relative_paths:
            src = templates_dir / relative_path
            dst = kernel_dir / relative_path
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
