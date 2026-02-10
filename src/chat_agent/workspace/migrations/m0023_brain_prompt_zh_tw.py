"""Migration to rewrite brain system prompt in Traditional Chinese."""

import shutil
from pathlib import Path

from .base import Migration


class M0023BrainPromptZhTw(Migration):
    """Rewrite brain system prompt in zh-TW with stronger memory_search trigger rules."""

    version = "0.6.2"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
