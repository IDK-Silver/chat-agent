"""Migrate builtin skills to SKILL.md frontmatter format."""

import shutil
from pathlib import Path

from .base import Migration

_DEPLOY = [
    "builtin-skills/index.md",
    "builtin-skills/discord-messaging/SKILL.md",
    "builtin-skills/skill-creator/SKILL.md",
    "builtin-skills/skill-installer/SKILL.md",
    "agents/brain/prompts/system.md",
]

_DELETE = [
    "builtin-skills/discord-messaging/guide.md",
    "builtin-skills/discord-messaging/meta.yaml",
    "builtin-skills/skill-create/guide.md",
]

_DELETE_DIRS = [
    "builtin-skills/skill-create",
]


class M0136SkillMdFormat(Migration):
    """Migrate builtin skills to SKILL.md frontmatter format."""

    version = "0.69.0"
    summary = (
        "技能系統升級：改用 SKILL.md frontmatter 格式、"
        "治理規則移入設定檔、新增 skill-installer"
    )

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _DEPLOY:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        for rel in _DELETE:
            old = kernel_dir / rel
            if old.exists():
                old.unlink()

        for rel in _DELETE_DIRS:
            old_dir = kernel_dir / rel
            if not old_dir.exists() or not old_dir.is_dir():
                continue
            remaining = list(old_dir.iterdir())
            if not remaining:
                old_dir.rmdir()
            elif len(remaining) == 1 and remaining[0].name == "index.md":
                remaining[0].unlink()
                old_dir.rmdir()
