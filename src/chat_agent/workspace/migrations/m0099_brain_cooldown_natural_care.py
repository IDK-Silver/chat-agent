"""Deploy brain prompt cooldown refinements for natural repeated care."""

import shutil
from pathlib import Path

from .base import Migration


class M0099BrainCooldownNaturalCare(Migration):
    """Update brain prompt cooldown rule to avoid suppressing natural concern."""

    version = "0.57.4"
    summary = "調整 Brain cooldown 原則：防重複催促話術，不阻止再次關心；有新跡象時可換角度自然回應"

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
