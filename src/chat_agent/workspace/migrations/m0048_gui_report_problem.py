"""Update GUI manager/worker prompts: report_problem, escalation, copy_screenshot, mismatch."""

import shutil
from pathlib import Path

from .base import Migration


class M0048GuiReportProblem(Migration):
    """Update GUI manager and worker system prompts."""

    version = "0.22.0"

    _PROMPT_FILES = [
        "agents/gui_manager/prompts/system.md",
        "agents/gui_worker/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
