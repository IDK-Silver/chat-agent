"""Add persona trigger rule to brain and reviewer prompts.

Brain gains a trigger condition for persona.md updates when user
redefines agent identity/values/emotional boundaries.
Post-reviewer gains explicit target_persona guidance.
"""

import shutil
from pathlib import Path

from .base import Migration


class M0040PersonaTrigger(Migration):
    """Add persona update trigger to brain and reviewer prompts."""

    version = "0.15.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        prompt_pairs = [
            ("brain", "system.md"),
            ("post_reviewer", "system.md"),
        ]
        for agent, prompt_file in prompt_pairs:
            src = templates_dir / "agents" / agent / "prompts" / prompt_file
            dst = kernel_dir / "agents" / agent / "prompts" / prompt_file
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
