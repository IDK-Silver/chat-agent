"""Update brain prompt with web_fetch prompt parameter and skill_rescan docs."""

import shutil
from pathlib import Path

from .base import Migration


class M0144WebFetchPromptDocs(Migration):
    """Deploy brain prompt with updated web_fetch tool description."""

    version = "0.74.2"
    summary = "Update brain prompt: web_fetch now requires prompt param"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
