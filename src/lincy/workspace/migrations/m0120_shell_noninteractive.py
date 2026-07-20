"""Deploy non-interactive execute_shell guidance in brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0120ShellNonInteractive(Migration):
    """Copy updated brain system.md with execute_shell boundary guidance."""

    version = "0.63.9"
    summary = "更新 brain prompt：execute_shell 僅限非互動式 shell，OAuth/GUI 需求需改走正確邊界"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
