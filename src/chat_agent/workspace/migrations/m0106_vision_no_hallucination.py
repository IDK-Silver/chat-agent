"""Harden vision agent prompt against character identification hallucination."""

import shutil
from pathlib import Path

from .base import Migration


class M0106VisionNoHallucination(Migration):
    """Rewrite vision agent system prompt to forbid external knowledge injection."""

    version = "0.59.0"
    summary = (
        "Vision agent prompt 強化：禁止用外部知識辨識角色名稱，"
        "文字必須原文照抄，角色只描述外觀不做身份推斷"
    )

    _PROMPT_FILES = [
        "agents/vision/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
