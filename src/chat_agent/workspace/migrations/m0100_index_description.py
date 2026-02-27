"""Deploy memory_editor planner prompt with index_description field."""

import shutil
from pathlib import Path

from .base import Migration


class M0100IndexDescription(Migration):
    """Planner outputs index_description for create_if_missing; replaces instruction[:80] hack."""

    version = "0.58.0"
    summary = "memory_editor planner 輸出 index_description 語意摘要，取代 instruction 截斷"

    _PROMPT_FILES = [
        "agents/memory_editor/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
