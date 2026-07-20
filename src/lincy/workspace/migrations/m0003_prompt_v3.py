"""Migration to v3 system prompt with comprehensive boot and during-conversation protocol."""

import shutil
from pathlib import Path

from .base import Migration


class M0003PromptV3(Migration):
    """Update brain system prompt to v3.

    Changes:
    - Fix path bug (.agent/memory/ -> memory/)
    - Add two-phase boot (read_file + index scan)
    - Add during-conversation trigger rules
    - Add shell & tool learning protocol
    """

    version = "0.3.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            shutil.copy2(src, dst)
