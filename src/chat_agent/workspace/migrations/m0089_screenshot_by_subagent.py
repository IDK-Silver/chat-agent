"""Add screenshot_by_subagent tool + GUIWorker describe prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0089ScreenshotBySubagent(Migration):
    """screenshot tool delegates to vision sub-agent, deploy describe prompt.

    - Brain prompt: add screenshot_by_subagent to tool table, update gui_task
      guidelines to use context-based screenshot delegation
    - GUIWorker: add describe.md prompt for describe_screen()
    """

    version = "0.54.0"
    summary = "screenshot 改為 screenshot_by_subagent 委派子代理分析，避免 brain context window 浪費"

    _PROMPT_FILES = [
        "agents/brain/prompts/system.md",
        "agents/gui_worker/prompts/describe.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
