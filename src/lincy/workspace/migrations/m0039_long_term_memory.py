"""Add long-term.md singleton memory file.

Creates memory/agent/long-term.md for persistent important items
(agreements, long-term TODOs, critical facts).
Updates brain system/shutdown prompts, post_reviewer prompt, and agent index.
"""

import shutil
from pathlib import Path

from .base import Migration


class M0039LongTermMemory(Migration):
    """Add long-term.md and update prompts for long-term memory support."""

    version = "0.14.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        memory_dir = kernel_dir.parent / "memory"

        # Copy long-term.md template (only if not yet present)
        long_term_dst = memory_dir / "agent" / "long-term.md"
        if not long_term_dst.exists():
            long_term_src = (
                templates_dir.parent / "memory" / "agent" / "long-term.md"
            )
            if long_term_src.exists():
                long_term_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(long_term_src, long_term_dst)

        # Copy updated prompts
        prompt_pairs = [
            ("brain", "system.md"),
            ("brain", "shutdown.md"),
            ("post_reviewer", "system.md"),
        ]
        for agent, prompt_file in prompt_pairs:
            src = templates_dir / "agents" / agent / "prompts" / prompt_file
            dst = kernel_dir / "agents" / agent / "prompts" / prompt_file
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # Update agent index from template
        src_index = templates_dir.parent / "memory" / "agent" / "index.md"
        dst_index = memory_dir / "agent" / "index.md"
        if src_index.exists() and dst_index.exists():
            shutil.copy2(src_index, dst_index)
