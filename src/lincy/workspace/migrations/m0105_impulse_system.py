"""Add impulse type to pending-thoughts system."""

import shutil
from pathlib import Path

from .base import Migration


class M0105ImpulseSystem(Migration):
    """Add impulse execution path: task/impulse type for pending-thoughts."""

    version = "0.58.0"
    summary = (
        "念頭系統新增 impulse 類型：pending-thoughts 條目區分 task/impulse，"
        "impulse 不走 blocked/cooldown 規則框架，由 agent 自行判斷是否行動"
    )

    _PROMPT_FILES = [
        "agents/brain/prompts/system.md",
        "agents/memory_editor/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
