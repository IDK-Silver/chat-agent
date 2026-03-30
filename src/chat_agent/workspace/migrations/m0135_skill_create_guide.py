"""Deploy updated builtin skill-create guide into live kernel."""

import shutil
from pathlib import Path

from .base import Migration

_FILES = [
    "builtin-skills/skill-create/guide.md",
]


class M0135SkillCreateGuide(Migration):
    """Copy the refreshed skill-create guide into live kernel files."""

    version = "0.68.2"
    summary = "更新內建 skill-create 指南：主內容改放 guide.md，index 連結自動維護，刪到空資料夾時會自動清理。"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
