"""Refresh brain prompt with state commit tool budget guidance."""

from pathlib import Path
import shutil

from .base import Migration


class M0155StateCommitToolBudget(Migration):
    """Deploy prompt rules for batching same-turn state commits."""

    version = "0.74.13"
    summary = "Brain prompt: agent_note / memory_edit 同輪批次提交與重複呼叫警告"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
