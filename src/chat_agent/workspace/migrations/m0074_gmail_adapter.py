"""Deploy Gmail adapter prompt changes and create contact map cache directory."""

import shutil
from pathlib import Path

from .base import Migration


class M0074GmailAdapter(Migration):
    """Update brain prompt with Gmail channel + contact resolution guidance."""

    version = "0.43.0"

    _PROMPT_FILES = [
        "agents/brain/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # Ensure cache directory exists for contact_map.json
        # kernel_dir is .agent/kernel; memory/cache is at .agent/memory/cache
        cache_dir = kernel_dir.parent / "memory" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
