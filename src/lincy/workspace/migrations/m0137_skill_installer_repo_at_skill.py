"""Normalize skill-installer examples to owner/repo@skill format."""

import shutil
from pathlib import Path

from .base import Migration


class M0137SkillInstallerRepoAtSkill(Migration):
    """Deploy the updated builtin skill-installer guide."""

    version = "0.69.1"
    summary = "統一 skill-installer 安裝範例為 owner/repo@skill 格式"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        rel = "builtin-skills/skill-installer/SKILL.md"
        src = templates_dir / rel
        dst = kernel_dir / rel
        if not src.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
