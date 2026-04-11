"""Deploy the iCloud-sync awareness prompt fragment into existing kernels."""

import shutil
from pathlib import Path

from .base import Migration

_FILES = [
    "agents/brain/prompts/fragments/icloud-sync-awareness.md",
]


class M0147ICloudSyncPromptFragment(Migration):
    """Copy the iCloud-sync awareness prompt fragment into the live kernel."""

    version = "0.74.5"
    summary = "補發 brain iCloud sync awareness fragment，避免舊 workspace 啟動時缺檔"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in _FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
