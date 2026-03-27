"""Add core values section to long-term.md and refresh prompts."""

import shutil
from pathlib import Path

from .base import Migration


class M0132LongTermCoreValues(Migration):
    """Add ## 核心價值 section to long-term.md; refresh brain and memory_editor prompts."""

    version = "0.67.0"
    summary = "long-term.md 新增核心價值 section；HEARTBEAT 流程從任務導向改為先想人"

    _PROMPT_FILES = [
        "agents/brain/prompts/system.md",
        "agents/memory_editor/prompts/system.md",
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # Refresh prompt files from templates
        for rel in self._PROMPT_FILES:
            src = templates_dir / rel
            dst = kernel_dir / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # Migrate existing long-term.md in memory/ (user data, not kernel)
        # kernel_dir parent is agent_os_dir
        agent_os_dir = kernel_dir.parent
        lt_path = agent_os_dir / "memory" / "agent" / "long-term.md"
        if not lt_path.exists():
            return

        content = lt_path.read_text(encoding="utf-8")

        # Already migrated
        if "## 核心價值" in content:
            return

        # Insert ## 核心價值 section before ## 約定
        if "## 約定" not in content:
            return

        new_section = (
            "## 核心價值\n"
            "\n"
            "<!-- 行為精神，上限 5 條。不是規則，是「我是誰」。格式：- 自由文字 -->\n"
            "\n"
        )
        content = content.replace("## 約定", new_section + "## 約定")

        # Update 解讀原則 to include core values explanation
        old_principle = (
            "- 只有「約定」區塊中的條目視為當前生效的行為規則。"
        )
        new_principle = (
            "- 「核心價值」定義 agent 的行為精神，每次行動前內化。"
            "它們不是可完成的任務，而是「我是誰」的一部分。\n"
            "- 只有「約定」區塊中的條目視為當前生效的行為規則。"
        )
        content = content.replace(old_principle, new_principle)

        lt_path.write_text(content, encoding="utf-8")
