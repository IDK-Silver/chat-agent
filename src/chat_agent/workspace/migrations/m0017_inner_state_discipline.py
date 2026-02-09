"""Migration to add inner-state discipline and trivial turn exemption."""

import shutil
from pathlib import Path

from .base import Migration


class M0017InnerStateDiscipline(Migration):
    """Copy updated brain + post_reviewer prompts with inner-state guardrails."""

    version = "0.5.11"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        mappings = [
            (
                templates_dir / "agents" / "brain" / "prompts" / "system.md",
                kernel_dir / "agents" / "brain" / "prompts" / "system.md",
            ),
            (
                templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md",
                kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md",
            ),
        ]

        for src, dst in mappings:
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
