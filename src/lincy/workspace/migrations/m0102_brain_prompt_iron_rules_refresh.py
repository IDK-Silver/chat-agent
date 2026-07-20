"""Deploy refreshed brain system prompt with iron-rules baseline."""

import shutil
from pathlib import Path

from .base import Migration


class M0102BrainPromptIronRulesRefresh(Migration):
    """Refresh brain system prompt from latest template."""

    version = "0.57.7"
    summary = "更新 Brain 系統提示：套用最新鐵則基線版本，修正既有 agent OS 的提示內容"

    _PROMPT_FILES = [
        "agents/brain/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
