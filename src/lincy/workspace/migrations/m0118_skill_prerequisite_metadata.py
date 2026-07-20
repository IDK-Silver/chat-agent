"""Deploy skill metadata for runtime prerequisite enforcement."""

import shutil
from pathlib import Path

from .base import Migration

_FILES = [
    "builtin-skills/discord-messaging/meta.yaml",
]


class M0118SkillPrerequisiteMetadata(Migration):
    """Publish runtime metadata for governed builtin skills."""

    version = "0.63.7"
    summary = "加入 skill prerequisite metadata，受管工具會先載入對應 guide 再執行"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
