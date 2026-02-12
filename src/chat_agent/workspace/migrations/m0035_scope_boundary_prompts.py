"""Add scope boundary to post_reviewer and memory_editor prompts.

Prevents sub-LLMs from using violation codes or error codes as content
moderation proxies (e.g. flagging NSFW content as repetitive_content).
"""

import shutil
from pathlib import Path

from .base import Migration


class M0035ScopeBoundaryPrompts(Migration):
    """Copy updated post_reviewer and memory_editor prompts with scope constraints."""

    version = "0.10.3"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        pairs = [
            ("post_reviewer", "system.md"),
            ("memory_editor", "system.md"),
        ]
        for agent, prompt_file in pairs:
            src = templates_dir / "agents" / agent / "prompts" / prompt_file
            dst = kernel_dir / "agents" / agent / "prompts" / prompt_file
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
