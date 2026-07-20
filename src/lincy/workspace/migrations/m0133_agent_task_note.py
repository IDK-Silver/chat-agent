"""Deploy agent_task + agent_note tool guidance in brain prompt."""

import shutil
from pathlib import Path

from .base import Migration


class M0133AgentTaskNote(Migration):
    """Copy updated brain system.md with agent_task and agent_note guidance."""

    version = "0.68.0"
    summary = (
        "新增 agent_task (todo + calendar) 與 agent_note (即時狀態追蹤) 工具。"
        "HEARTBEAT 自動帶入 pending tasks；agent_note 每 turn 注入 context，"
        "trigger 命中時提醒更新。"
    )

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "brain" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "brain" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
