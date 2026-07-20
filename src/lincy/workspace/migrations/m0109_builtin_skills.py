"""Add kernel/builtin-skills/ directory with skill-create guide."""

import shutil
from pathlib import Path

from .base import Migration

_BUILTIN_SKILL_FILES = [
    "builtin-skills/index.md",
    "builtin-skills/skill-create/guide.md",
]

_PROMPT_FILES = [
    "agents/brain/prompts/system.md",
]


class M0109BuiltinSkills(Migration):
    version = "0.62.0"
    summary = (
        "kernel/builtin-skills/ -- "
        "system-managed skills directory with skill-create guide; "
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
