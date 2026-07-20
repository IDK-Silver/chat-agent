"""Migration to remove memory_editor sub-LLM prompts (now deterministic-only)."""

import shutil
from pathlib import Path

from .base import Migration


class M0025RemoveEditorLlm(Migration):
    """Remove memory_editor agent prompts — editor no longer uses LLM."""

    version = "0.6.4"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        editor_dir = kernel_dir / "agents" / "memory_editor"
        if editor_dir.exists():
            shutil.rmtree(editor_dir)
