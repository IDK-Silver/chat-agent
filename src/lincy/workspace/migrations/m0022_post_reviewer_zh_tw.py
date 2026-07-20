"""Migration to update post-reviewer prompt to Traditional Chinese with stronger trivial exemption."""

import shutil
from pathlib import Path

from .base import Migration


class M0022PostReviewerZhTw(Migration):
    """Rewrite post-reviewer prompt in zh-TW with clearer trivial turn exemption."""

    version = "0.6.1"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
