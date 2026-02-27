"""Annotate Copilot tool-loop thinking visibility changes for agent upgrade notes."""

from pathlib import Path

from .base import Migration


class M0103CopilotReasoningVisibility(Migration):
    """No-op migration to surface Copilot reasoning visibility behavior changes."""

    version = "0.57.8"
    summary = (
        "更新 Copilot 推理可見性：tool loop 回應若含 reasoning_content，"
        "TUI 會顯示 THINKING 區塊並標註字元數"
    )

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        del kernel_dir, templates_dir
        return
