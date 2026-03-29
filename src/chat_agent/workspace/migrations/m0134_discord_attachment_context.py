"""Deploy updated Discord attachment guidance in brain/skill prompts."""

import shutil
from pathlib import Path

from .base import Migration

_FILES = [
    "agents/brain/prompts/system.md",
    "builtin-skills/discord-messaging/guide.md",
]


class M0134DiscordAttachmentContext(Migration):
    """Copy updated Discord attachment guidance into live kernel files."""

    version = "0.68.1"
    summary = "統一 Discord 附件心智模型：先看 local_path/url，再決定如何處理；不要只把圖片當附件。"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
