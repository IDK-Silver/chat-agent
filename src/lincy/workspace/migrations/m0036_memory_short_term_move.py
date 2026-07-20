"""Move short-term.md into memory/agent/ and remove obsolete config.md.

- Move memory/short-term.md -> memory/agent/short-term.md
- Delete memory/agent/config.md
- Copy updated prompt templates (brain, post_reviewer, shutdown_reviewer)
- Update memory/agent/index.md
"""

import shutil
from pathlib import Path

from .base import Migration


class M0036MemoryShortTermMove(Migration):
    """Relocate short-term.md under agent/ and remove config.md."""

    version = "0.11.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # 1. Copy updated prompt templates.
        prompt_pairs = [
            ("brain", "system.md"),
            ("brain", "shutdown.md"),
            ("post_reviewer", "system.md"),
            ("shutdown_reviewer", "system.md"),
        ]
        for agent, prompt_file in prompt_pairs:
            src = templates_dir / "agents" / agent / "prompts" / prompt_file
            dst = kernel_dir / "agents" / agent / "prompts" / prompt_file
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # 2. Move memory/short-term.md -> memory/agent/short-term.md.
        memory_dir = kernel_dir.parent / "memory"
        old = memory_dir / "short-term.md"
        new = memory_dir / "agent" / "short-term.md"
        if old.exists() and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))

        # 3. Delete memory/agent/config.md.
        config = memory_dir / "agent" / "config.md"
        if config.exists():
            config.unlink()

        # 4. Update memory/agent/index.md from template.
        src_index = (
            templates_dir.parent / "memory" / "agent" / "index.md"
        )
        dst_index = memory_dir / "agent" / "index.md"
        if src_index.exists() and dst_index.exists():
            shutil.copy2(src_index, dst_index)
