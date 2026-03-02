"""Add kernel/builtin-skills/ directory with memory-maintenance skill."""

import shutil
from pathlib import Path

from .base import Migration

_BUILTIN_SKILL_FILES = [
    "builtin-skills/index.md",
    "builtin-skills/memory-maintenance/guide.md",
    "builtin-skills/memory-maintenance/rules.md",
]

_PROMPT_FILES = [
    "agents/brain/prompts/system.md",
]


class M0109BuiltinSkills(Migration):
    version = "0.62.0"
    summary = (
        "kernel/builtin-skills/ -- "
        "system-managed skills directory with memory-maintenance as first entry; "
        "brain iron rule 9 expanded to dual-index lookup"
    )

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _BUILTIN_SKILL_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        for rel in _PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
