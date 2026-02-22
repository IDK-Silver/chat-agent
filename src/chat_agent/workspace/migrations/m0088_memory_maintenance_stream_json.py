"""Upgrade memory-maintenance skill guide to Claude Code stream-json command format."""

import shutil
from pathlib import Path

from .base import Migration

_GUIDE_REL_PATH = "memory/agent/skills/memory-maintenance/guide.md"


class M0088MemoryMaintenanceStreamJson(Migration):
    """Deploy updated memory-maintenance guide with Claude Code stream-json flags."""

    version = "0.53.1"
    summary = "memory-maintenance skill 指南改用 Claude Code stream-json 指令格式（直接覆蓋 guide.md）"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir.parent / _GUIDE_REL_PATH
        dst = kernel_dir.parent / _GUIDE_REL_PATH
        if not src.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
