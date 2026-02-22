"""Auto-maintain index.md links, format validation, and file health warnings."""

import shutil
from pathlib import Path

from .base import Migration

_PROMPT_COPIES = [
    ("agents/brain/prompts/system.md", "agents/brain/prompts/system.md"),
    ("agents/memory_editor/prompts/system.md", "agents/memory_editor/prompts/system.md"),
]

_SKILL_FILES = [
    "memory/agent/skills/memory-maintenance/guide.md",
    "memory/agent/skills/memory-maintenance/rules.md",
]


class M0087MemoryEditIndexWarnings(Migration):
    """Deploy index auto-maintenance, format validation, and warnings."""

    version = "0.53.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # Copy updated prompts to kernel
        for src_rel, dst_rel in _PROMPT_COPIES:
            src = templates_dir / src_rel
            dst = kernel_dir / dst_rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # Deploy memory-maintenance skill to memory/
        memory_dir = kernel_dir.parent / "memory"
        for rel_path in _SKILL_FILES:
            src = templates_dir.parent / rel_path
            dst = memory_dir.parent / rel_path
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
