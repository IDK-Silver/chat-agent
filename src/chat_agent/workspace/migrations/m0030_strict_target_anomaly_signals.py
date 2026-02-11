"""Migration to strict target/anomaly reviewer prompts."""

import shutil
from pathlib import Path

from .base import Migration


class M0030StrictTargetAnomalySignals(Migration):
    """Copy strict target/anomaly prompts for brain and post reviewer."""

    version = "0.9.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        relative_paths = [
            "agents/brain/prompts/system.md",
            "agents/brain/prompts/shutdown.md",
            "agents/post_reviewer/prompts/system.md",
            "agents/post_reviewer/prompts/parse-retry.md",
            "agents/shutdown_reviewer/prompts/system.md",
            "agents/shutdown_reviewer/prompts/parse-retry.md",
        ]
        for relative_path in relative_paths:
            src = templates_dir / relative_path
            dst = kernel_dir / relative_path
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
