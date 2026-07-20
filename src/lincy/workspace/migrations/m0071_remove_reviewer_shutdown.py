"""Remove reviewer and shutdown agent templates from kernel."""

import shutil
from pathlib import Path

from .base import Migration


class M0071RemoveReviewerShutdown(Migration):
    """Remove post_reviewer, progress_reviewer, shutdown_reviewer agents and brain shutdown prompt."""

    version = "0.40.0"

    _REMOVE_AGENT_DIRS = [
        "agents/post_reviewer",
        "agents/progress_reviewer",
        "agents/shutdown_reviewer",
    ]

    _REMOVE_FILES = [
        "agents/brain/prompts/shutdown.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._REMOVE_AGENT_DIRS:
            agent_dir = kernel_dir / rel
            if agent_dir.exists():
                shutil.rmtree(agent_dir)

        for rel in self._REMOVE_FILES:
            path = kernel_dir / rel
            if path.exists():
                path.unlink()
