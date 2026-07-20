"""Deploy shell_task guidance in brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0121ShellTask(Migration):
    """Copy updated brain system.md with shell_task guidance."""

    version = "0.64.0"
    summary = "更新 brain prompt：新增 shell_task 背景 shell 任務邊界與使用指引"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
