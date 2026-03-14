"""Deploy kernel-managed brain prompt fragments for optional guidance blocks."""

import shutil
from pathlib import Path

from .base import Migration

_FILES = [
    "agents/brain/prompts/system.md",
    "agents/brain/prompts/fragments/send-message-batch-guidance.md",
]


class M0125BrainPromptFragments(Migration):
    """Deploy prompt-fragment files referenced by the brain system prompt."""

    version = "0.66.2"
    summary = "更新 Brain prompt 載入：可選 guidance 改由 kernel fragment 檔案管理"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
