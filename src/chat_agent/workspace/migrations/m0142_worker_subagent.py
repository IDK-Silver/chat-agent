"""Deploy worker subagent prompt and update brain prompt with worker tool docs."""

import shutil
from pathlib import Path

from .base import Migration

_PROMPT_COPIES = [
    ("agents/worker/prompts/system.md", "agents/worker/prompts/system.md"),
    ("agents/brain/prompts/system.md", "agents/brain/prompts/system.md"),
]


class M0142WorkerSubagent(Migration):
    """Deploy worker prompt and update brain prompt with worker tool guidance."""

    version = "0.74.0"
    summary = "Add worker subagent; update brain prompt with worker tool docs"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for src_rel, dst_rel in _PROMPT_COPIES:
            src = templates_dir / src_rel
            dst = kernel_dir / dst_rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
