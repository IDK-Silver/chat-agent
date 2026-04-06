"""Update brain system prompt with worker tool documentation."""

import shutil
from pathlib import Path

from .base import Migration


class M0143BrainWorkerToolDocs(Migration):
    """Deploy brain prompt with worker tool table entry and efficiency guidance."""

    version = "0.74.1"
    summary = "Add worker tool docs to brain system prompt"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
