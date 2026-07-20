"""Migration to agents/ directory structure."""

import shutil
from pathlib import Path

from .base import Migration


class M0002AgentsStructure(Migration):
    """Restructure system-prompts/ to agents/{agent}/prompts/."""

    version = "0.2.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # Remove old system-prompts/
        old_prompts = kernel_dir / "system-prompts"
        if old_prompts.exists():
            shutil.rmtree(old_prompts)

        # Copy new agents/ structure from templates
        agents_src = templates_dir / "agents"
        agents_dst = kernel_dir / "agents"
        if agents_src.exists():
            if agents_dst.exists():
                shutil.rmtree(agents_dst)
            shutil.copytree(agents_src, agents_dst)
