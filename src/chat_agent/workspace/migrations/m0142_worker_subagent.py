"""Deploy worker subagent system prompt template."""

import shutil
from pathlib import Path

from .base import Migration


class M0142WorkerSubagent(Migration):
    """Copy worker subagent prompt into kernel workspace."""

    version = "0.74.0"
    summary = "Add worker subagent system prompt"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "worker" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "worker" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
