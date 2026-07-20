"""Add attachments parameter to send_message tool in brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0078SendMessageAttachments(Migration):
    """Update brain prompt with send_message attachments support."""

    version = "0.46.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents/brain/prompts/system.md"
        dst = kernel_dir / "agents/brain/prompts/system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
