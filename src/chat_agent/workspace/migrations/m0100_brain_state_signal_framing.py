"""Deploy brain prompt framing for reading events as partner-state signals."""

import shutil
from pathlib import Path

from .base import Migration


class M0100BrainStateSignalFraming(Migration):
    """Update brain prompt to frame all events as state signals before action."""

    version = "0.57.5"
    summary = "強化 Brain 人感 framing：先把用戶訊息/系統事件/時間視為對方狀態訊號，再決定如何回應"

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
