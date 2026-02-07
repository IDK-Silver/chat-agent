"""Migration to split reviewer prompts into dedicated agent directories."""

import shutil
from pathlib import Path

from .base import Migration


class M0006ReviewerAgents(Migration):
    """Move reviewer prompts from brain/ to pre_reviewer/ and post_reviewer/."""

    version = "0.5.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        mappings = [
            (
                kernel_dir / "agents" / "brain" / "prompts" / "reviewer-pre.md",
                kernel_dir / "agents" / "pre_reviewer" / "prompts" / "system.md",
                templates_dir / "agents" / "pre_reviewer" / "prompts" / "system.md",
            ),
            (
                kernel_dir / "agents" / "brain" / "prompts" / "reviewer-post.md",
                kernel_dir / "agents" / "post_reviewer" / "prompts" / "system.md",
                templates_dir / "agents" / "post_reviewer" / "prompts" / "system.md",
            ),
        ]

        for old_path, new_path, template_path in mappings:
            new_path.parent.mkdir(parents=True, exist_ok=True)

            if old_path.exists():
                shutil.copy2(old_path, new_path)
                old_path.unlink()
                continue

            if template_path.exists():
                shutil.copy2(template_path, new_path)
