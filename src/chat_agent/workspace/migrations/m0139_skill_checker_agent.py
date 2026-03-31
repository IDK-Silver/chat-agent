"""Deploy the skill_checker prompt template into kernel."""

import shutil
from pathlib import Path

from .base import Migration


class M0139SkillCheckerAgent(Migration):
    """Copy the skill_checker system prompt into existing workspaces."""

    version = "0.71.1"
    summary = "新增 skill_checker 子代理，用於主模型前的 skill 預載判斷"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "skill_checker" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "skill_checker" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
