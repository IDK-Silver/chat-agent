"""Refresh brain prompt with schedule_action batch guidance."""

from pathlib import Path
import shutil

from .base import Migration


class M0156ScheduleActionBatch(Migration):
    """Deploy prompt rules for batch-only schedule_action mutations."""

    version = "0.74.14"
    summary = "Brain prompt: schedule_action 改為 batch_add / batch_remove 並限制同輪重複提交"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
